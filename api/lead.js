export const config = { runtime: 'edge' };

const ALLOWED_ORIGINS = new Set([
  'https://amaps-pro.ru',
  'https://www.amaps-pro.ru'
]);

const MAX_FILE_BYTES = 4 * 1024 * 1024;
const ALLOWED_TYPES = new Set([
  'application/pdf',
  'application/msword',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/vnd.ms-excel',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'image/jpeg',
  'image/png'
]);

function json(data, status = 200, origin = '') {
  const headers = {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
    'x-content-type-options': 'nosniff'
  };
  if (ALLOWED_ORIGINS.has(origin)) {
    headers['access-control-allow-origin'] = origin;
    headers['vary'] = 'Origin';
  }
  return new Response(JSON.stringify(data), { status, headers });
}

function clean(value, max = 2000) {
  return String(value ?? '').replace(/[\u0000-\u001F\u007F]/g, ' ').trim().slice(0, max);
}

function escapeHtml(value) {
  return clean(value, 5000)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function isEmail(value) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(value || '').trim());
}

function toBase64(arrayBuffer) {
  const bytes = new Uint8Array(arrayBuffer);
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, Math.min(i + chunk, bytes.length)));
  }
  return btoa(binary);
}

export default async function handler(request) {
  const origin = request.headers.get('origin') || '';

  if (request.method === 'OPTIONS') {
    if (!ALLOWED_ORIGINS.has(origin)) return json({ ok: false }, 403, origin);
    return new Response(null, {
      status: 204,
      headers: {
        'access-control-allow-origin': origin,
        'access-control-allow-methods': 'POST, OPTIONS',
        'access-control-allow-headers': 'content-type',
        'access-control-max-age': '86400',
        'vary': 'Origin'
      }
    });
  }

  if (request.method !== 'POST') return json({ ok: false, error: 'Метод не поддерживается.' }, 405, origin);
  if (!ALLOWED_ORIGINS.has(origin)) return json({ ok: false, error: 'Источник запроса не разрешён.' }, 403, origin);

  const apiKey = process.env.RESEND_API_KEY;
  const toEmail = process.env.AMAPS_TO_EMAIL || 'info@amaps-pro.ru';
  const fromEmail = process.env.AMAPS_FROM_EMAIL || 'АМАПС <info@amaps-pro.ru>';

  if (!apiKey) return json({ ok: false, error: 'Сервер отправки не настроен.' }, 503, origin);

  let form;
  try {
    form = await request.formData();
  } catch {
    return json({ ok: false, error: 'Не удалось прочитать данные формы.' }, 400, origin);
  }

  // Honeypot: обычный посетитель это поле не видит.
  if (clean(form.get('website'), 200)) return json({ ok: true }, 200, origin);

  const role = clean(form.get('role'), 40);
  const company = clean(form.get('company'), 180);
  const name = clean(form.get('name'), 120);
  const contact = clean(form.get('contact'), 180);
  const task = clean(form.get('task'), 5000);

  if (!['Заказчик', 'Исполнитель'].includes(role)) return json({ ok: false, error: 'Укажите тип обращения.' }, 400, origin);
  if (!company || !name || !contact || !task) return json({ ok: false, error: 'Заполните обязательные поля.' }, 400, origin);

  const attachment = form.get('attachment');
  const attachments = [];
  if (attachment && typeof attachment === 'object' && 'size' in attachment && attachment.size > 0) {
    if (attachment.size > MAX_FILE_BYTES) return json({ ok: false, error: 'Файл больше 4 МБ.' }, 413, origin);
    if (!ALLOWED_TYPES.has(attachment.type)) return json({ ok: false, error: 'Недопустимый формат файла.' }, 415, origin);
    attachments.push({
      filename: clean(attachment.name, 180) || 'attachment',
      content: toBase64(await attachment.arrayBuffer())
    });
  }

  const subject = `${role}: обращение с сайта АМАПС — ${company}`;
  const text = [
    `Тип обращения: ${role}`,
    `Компания: ${company}`,
    `Имя: ${name}`,
    `Контакт: ${contact}`,
    '',
    'Описание:',
    task,
    '',
    'Источник: форма amaps-pro.ru'
  ].join('\n');

  const html = `
    <h2>Новое обращение с сайта АМАПС</h2>
    <p><strong>Тип:</strong> ${escapeHtml(role)}</p>
    <p><strong>Компания:</strong> ${escapeHtml(company)}</p>
    <p><strong>Имя:</strong> ${escapeHtml(name)}</p>
    <p><strong>Контакт:</strong> ${escapeHtml(contact)}</p>
    <p><strong>Описание:</strong></p>
    <p style="white-space:pre-wrap">${escapeHtml(task)}</p>
    <hr>
    <p style="color:#667">Источник: форма amaps-pro.ru</p>
  `;

  const payload = {
    from: fromEmail,
    to: [toEmail],
    subject,
    text,
    html
  };

  if (isEmail(contact)) payload.reply_to = contact;
  if (attachments.length) payload.attachments = attachments;

  let sendResponse;
  try {
    sendResponse = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: {
        'authorization': `Bearer ${apiKey}`,
        'content-type': 'application/json'
      },
      body: JSON.stringify(payload)
    });
  } catch {
    return json({ ok: false, error: 'Сервис отправки временно недоступен.' }, 502, origin);
  }

  if (!sendResponse.ok) {
    return json({ ok: false, error: 'Не удалось отправить обращение. Попробуйте ещё раз или напишите на info@amaps-pro.ru.' }, 502, origin);
  }

  return json({ ok: true, message: 'Обращение отправлено. АМАПС получил ваши данные.' }, 200, origin);
}
