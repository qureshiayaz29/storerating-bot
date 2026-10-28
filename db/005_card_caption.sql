-- Store each Card's caption with its Telegram file_id, so the send job only reads and sends.

begin;

alter table cards add column caption text;

commit;
