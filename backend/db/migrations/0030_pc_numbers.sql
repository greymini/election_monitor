-- 0030: correct the parliamentary-constituency numbers seeded by 0014.
--
-- 0014 numbered the three PCs 11 (Giridih), 4 (Kodarma) and 7 (Ranchi). 11 is
-- Giridih's *district* number; the PC numbers in CEO Jharkhand's AC/PC list
-- (ceo.jharkhand.gov.in/ACPCList.html) and in the Form 20 headers ("06 GIRIDIH
-- PARLIAMENTARY CONSTITUENCY") are 6 Giridih, 5 Kodarma and 8 Ranchi.
-- None of the new numbers is in use, so the order of the updates is free.

UPDATE pc SET pc_number = 6 WHERE state_id = 20 AND pc_number = 11 AND name_en = 'Giridih';
UPDATE pc SET pc_number = 5 WHERE state_id = 20 AND pc_number = 4  AND name_en = 'Kodarma';
UPDATE pc SET pc_number = 8 WHERE state_id = 20 AND pc_number = 7  AND name_en = 'Ranchi';
