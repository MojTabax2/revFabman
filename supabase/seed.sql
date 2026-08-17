-- Optional dev seed: one machine + one member with a badge + permission.
-- Run after 0001_init.sql. Safe to skip in production.

insert into machines (name, display_name)
values ('laser-1', 'Laser Cutter 1')
on conflict (name) do nothing;

-- Sample member. Replace the uid with a real OMNIKEY tap when you have one.
insert into members (name, rfid_uid)
values ('Test User', '043f6afba2')
on conflict (rfid_uid) do nothing;

insert into permissions (member_id, machine_id)
select mem.id, mac.id
from members mem, machines mac
where mem.name = 'Test User' and mac.name = 'laser-1'
on conflict do nothing;
