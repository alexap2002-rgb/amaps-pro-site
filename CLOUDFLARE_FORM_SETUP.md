# Серверная форма АМАПС через Cloudflare Worker

## Зачем
GitHub Pages остаётся хостингом сайта. Cloudflare Worker принимает форму и отправляет письмо через Resend. Секретный ключ не попадает в браузер.

## Рекомендуемый адрес
forms.amaps-pro.ru

## Переменные Worker
- RESEND_API_KEY — секрет
- AMAPS_TO_EMAIL — info@amaps-pro.ru
- AMAPS_FROM_EMAIL — АМАПС <info@amaps-pro.ru>

## Развёртывание через панель Cloudflare
1. Workers & Pages → Create application → Worker.
2. Вставить содержимое cloudflare-worker.js и Deploy.
3. Settings → Variables and Secrets → Add:
   - RESEND_API_KEY как Secret.
   - AMAPS_TO_EMAIL как Variable.
   - AMAPS_FROM_EMAIL как Variable.
4. Settings → Domains & Routes → Add → Custom Domain.
5. Указать forms.amaps-pro.ru.
6. После публикации проверить POST-запрос и затем указать https://forms.amaps-pro.ru в meta amaps-form-endpoint в index.html.

## Важно
Не добавлять реальный RESEND_API_KEY в GitHub.
