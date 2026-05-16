// Netlify Function: /.netlify/functions/ask
//
// Receives a user question + the current data payload, asks Claude,
// returns the answer. Stateless — the client sends along the full data
// each time so the function doesn't need its own storage.
//
// Env vars (set in Netlify dashboard):
//   ANTHROPIC_API_KEY      — required
//   CLAUDE_MODEL           — optional, defaults to claude-sonnet-4-5
//
// Cost shape: each call sends ~30-60kB of health JSON + a question.
// At Sonnet 4.5 input rates (~$3/MTok) one question is well under
// a cent. No pre-computed insights here — those live in the static
// build (insights.json).

const SYSTEM_PROMPT = `You are Claude, talking to Matt about his Apple Health data.
Matt has Parkinson's disease. The dashboard he's looking at shows 90 days
of metrics with walking gait as the headline (walking_speed_m_s, asymmetry,
double-support, step length), plus activity, cardio, sleep and hydration.

Tone (HARD RULES):
- Warmer than generic AI. Recognise that Parkinson's is hard. Never use
  empty cheerleading or exclamation marks. Warmth comes from substance.
- Frame trends, not single days. Steady numbers with a progressive
  condition are a win — call them that.
- When something looks off, offer ONE concrete lever (hydration, magnesium,
  walk timing, dinner earlier). Not a list. Not a lecture.
- No clinical/medical advice. No drug talk. Lifestyle levers only.
- Plain prose, no markdown, no bullets, no asterisks, no headings. Speak.
- Concise. Most answers should be 1-3 sentences. Longer only if the
  question genuinely demands it (e.g. "summarise the last month").

You have access to today's metrics and 30-90 days of history attached
below. The data structure mirrors what the dashboard shows.

Today's date is provided in the data.today field. The data is current as
of data.built_at.`;

exports.handler = async function (event) {
  if (event.httpMethod !== 'POST') {
    return jsonResponse(405, { error: 'POST only' });
  }

  let body;
  try {
    body = JSON.parse(event.body || '{}');
  } catch (e) {
    return jsonResponse(400, { error: 'Invalid JSON body' });
  }

  const { question, data, insights, history } = body;
  if (!question || typeof question !== 'string') {
    return jsonResponse(400, { error: 'Missing "question" string' });
  }

  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return jsonResponse(500, {
      error:
        'ANTHROPIC_API_KEY is not set on this Netlify site. Add it under ' +
        'Site settings → Environment variables, then redeploy.',
    });
  }

  const model = process.env.CLAUDE_MODEL || 'claude-sonnet-4-5';

  // Build the conversation: prior turns + a final user turn that
  // includes the data payload alongside the question.
  const dataSummary = JSON.stringify(
    { data: data || {}, insights: insights || null },
    null,
    0,
  );
  const messages = [];
  for (const m of Array.isArray(history) ? history.slice(-10) : []) {
    if (!m || !m.role || !m.content) continue;
    messages.push({ role: m.role, content: String(m.content) });
  }
  messages.push({
    role: 'user',
    content: `Health data (JSON):\n${dataSummary}\n\nMatt's question: ${question}`,
  });

  try {
    const r = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'x-api-key': apiKey,
        'anthropic-version': '2023-06-01',
        'content-type': 'application/json',
      },
      body: JSON.stringify({
        model,
        max_tokens: 800,
        system: SYSTEM_PROMPT,
        messages,
      }),
    });
    if (!r.ok) {
      const text = await r.text();
      return jsonResponse(r.status, {
        error: 'Claude API error',
        detail: text.slice(0, 400),
      });
    }
    const result = await r.json();
    const answer = (result.content || [])
      .filter(b => b.type === 'text')
      .map(b => b.text)
      .join('')
      .trim();
    return jsonResponse(200, { answer, model: result.model });
  } catch (err) {
    return jsonResponse(500, {
      error: 'Network error talking to Claude',
      detail: String(err).slice(0, 300),
    });
  }
};

function jsonResponse(statusCode, payload) {
  return {
    statusCode,
    headers: {
      'content-type': 'application/json',
      'access-control-allow-origin': '*',
    },
    body: JSON.stringify(payload),
  };
}
