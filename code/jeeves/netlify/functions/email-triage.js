// Netlify Function: Scheduled Email Triage for Jeeves
const { gmail_v1 } = require('googleapis').google;
const Anthropic = require('@anthropic-ai/sdk');
const anthropic = new Anthropic({ apiKey: process.env.CLAUDE_API_KEY });
const gmail = gmail_v1({
  version: 'v1',
  auth: new (require('google-auth-library')).JWT({
    email: process.env.GMAIL_SERVICE_ACCOUNT_EMAIL,
    key: process.env.GMAIL_SERVICE_ACCOUNT_KEY,
    scopes: ['https://www.googleapis.com/auth/gmail.readonly'],
  }),
});
const PRIORITY_SENDERS = {
  republic: ['campaigns@republic.co', 'support@republic.eu', 'tom.field@republic.eu'],
  bgv: ['team@bethnalgreenventures.com', 'yumi@bgv.co', 'rob@bgv.co', 'melanie@bgv.co'],
  marriott: ['frances@marriottharrison.com', 'ivana@marriottharrison.com'],
  rpgcc: ['AChandarana@rpgcc.co.uk'],
};
function buildGmailQuery() {
  const allSenders = Object.values(PRIORITY_SENDERS).flat();
  const fromQuery = allSenders.map(email => `from:${email}`).join(' OR ');
  return `(${fromQuery}) is:unread newer_than:1h`;
}
async function getPriorityContext() {
  try {
    const drive = require('googleapis').google.drive({ version: 'v3' });
    const response = await drive.files.export({
      fileId: process.env.PRIORITY_CONTEXT_DOC_ID,
      mimeType: 'text/plain',
    });
    return response.data;
  } catch (error) {
    console.error('Failed to fetch priority context:', error);
    return 'Context unavailable';
  }
}
async function triageWithHaiku(emailSubject, emailBody) {
  const response = await anthropic.messages.create({
    model: 'claude-haiku-3-5-20250514',
    max_tokens: 200,
    messages: [{
      role: 'user',
      content: `QUICK TRIAGE: Is this email about (1) term sheet/Republic, (2) SEIS/shareholder docs, (3) due diligence, or (4) urgent/blocking?\nSubject: ${emailSubject}\nBody preview: ${emailBody.substring(0, 300)}\nRespond with ONE category and confidence (high/med/low).`,
    }],
  });
  return response.content[0].text;
}
async function analyzeWithSonnet(email, priorityContext) {
  const response = await anthropic.messages.create({
    model: 'claude-sonnet-4-20250514',
    max_tokens: 500,
    messages: [{
      role: 'user',
      content: `You are Jeeves, an AI assistant managing critical emails for a healthcare startup founder during fundraising.\n\nCURRENT PRIORITY CONTEXT:\n${priorityContext}\n\nINCOMING EMAIL:\nFrom: ${email.from}\nSubject: ${email.subject}\nBody: ${email.body.substring(0, 500)}\n\nTASK: Decide what action Jeeves should take: (1) URGENT, (2) ACTIONABLE, (3) INFORMATIONAL, or (4) ARCHIVE. Suggest relevant docs to pull and draft response if needed.`,
    }],
  });
  return response.content[0].text;
}
async function fetchPriorityEmails() {
  try {
    const query = buildGmailQuery();
    const result = await gmail.users.messages.list({ userId: 'me', q: query, maxResults: 10 });
    if (!result.data.messages) return [];
    const messages = await Promise.all(result.data.messages.map(msg => gmail.users.messages.get({ userId: 'me', id: msg.id, format: 'full' })));
    return messages.map(msg => {
      const headers = msg.data.payload.headers;
      const body = msg.data.payload.parts?.[0]?.body?.data || msg.data.payload.body?.data || '';
      return {
        id: msg.data.id,
        from: headers.find(h => h.name === 'From')?.value || 'Unknown',
        subject: headers.find(h => h.name === 'Subject')?.value || '(No subject)',
        date: headers.find(h => h.name === 'Date')?.value || '',
        body: Buffer.from(body, 'base64').toString('utf-8').substring(0, 1000),
      };
    });
  } catch (error) {
    console.error('Failed to fetch emails:', error);
    return [];
  }
}
async function sendTelegramMessage(summary) {
  try {
    const response = await fetch('https://api.telegram.org/bot' + process.env.TELEGRAM_BOT_TOKEN + '/sendMessage', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: process.env.TELEGRAM_CHAT_ID, text: summary, parse_mode: 'Markdown' }),
    });
    if (!response.ok) console.error('Telegram send failed:', await response.text());
  } catch (error) {
    console.error('Failed to send Telegram message:', error);
  }
}
exports.handler = async (event) => {
  console.log('Jeeves email triage triggered at:', new Date().toISOString());
  try {
    const emails = await fetchPriorityEmails();
    if (emails.length === 0) {
      console.log('No priority emails found in last hour.');
      return { statusCode: 200, body: 'No emails' };
    }
    const priorityContext = await getPriorityContext();
    const analysis = [];
    for (const email of emails) {
      console.log(`Processing: ${email.subject} from ${email.from}`);
      const triageResult = await triageWithHaiku(email.subject, email.body);
      console.log(`Triage: ${triageResult}`);
      const analysis_result = await analyzeWithSonnet(email, priorityContext);
      analysis.push({ email: email.subject, triage: triageResult, analysis: analysis_result });
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    const summary = `📧 **Jeeves Email Triage** (${emails.length} priority emails)\n\n` + analysis.map(a => `**${a.email}**\n_Triage:_ ${a.triage}\n\n${a.analysis}\n---`).join('\n');
    await sendTelegramMessage(summary);
    return { statusCode: 200, body: JSON.stringify({ processed: emails.length, analysis }) };
  } catch (error) {
    console.error('Email triage failed:', error);
    await sendTelegramMessage(`❌ Jeeves email triage failed: ${error.message}`);
    return { statusCode: 500, body: JSON.stringify({ error: error.message }) };
  }
};
