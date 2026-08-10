MERGE `${PROJECT}.${DATASET}.ref_drug_class` T
USING (
  SELECT * FROM UNNEST([
    STRUCT('meloxicam'   AS drug_name, 'NSAID'            AS drug_class, TRUE  AS is_anti_inflammatory),
    ('ibuprofen',        'NSAID',                    TRUE),
    ('naproxen',         'NSAID',                    TRUE),
    ('diclofenac',       'NSAID',                    TRUE),
    ('celecoxib',        'NSAID (COX-2)',            TRUE),
    ('indomethacin',     'NSAID',                    TRUE),
    ('methylprednisolone','corticosteroid',          TRUE),
    ('prednisone',       'corticosteroid',           TRUE),
    ('triamcinolone',    'corticosteroid',           TRUE),
    ('dexamethasone',    'corticosteroid',           TRUE),
    ('acetaminophen',    'analgesic',                FALSE),
    ('tramadol',         'opioid analgesic',         FALSE),
    ('hydrocodone-acetaminophen', 'opioid analgesic', FALSE),
    ('cyclobenzaprine',  'muscle relaxant',          FALSE),
    ('methocarbamol',    'muscle relaxant',          FALSE),
    ('tizanidine',       'muscle relaxant',          FALSE),
    ('gabapentin',       'anticonvulsant / neuropathic', FALSE),
    ('duloxetine',       'SNRI',                     FALSE),
    ('nebivolol',        'beta blocker',             FALSE),
    ('olmesartan-amlodipin-hcthiazid', 'antihypertensive combination', FALSE),
    ('lisinopril',       'ACE inhibitor',            FALSE),
    ('metformin',        'biguanide',                FALSE),
    ('atorvastatin',     'statin',                   FALSE),
    ('levothyroxine',    'thyroid hormone',          FALSE),
    ('omeprazole',       'proton pump inhibitor',    FALSE),
    ('vitamin d3',       'supplement',               FALSE),
    ('calcium carbonate','supplement',               FALSE),
    ('alendronate',      'bisphosphonate',           FALSE)
  ])
) S
ON LOWER(T.drug_name) = LOWER(S.drug_name)
WHEN MATCHED THEN UPDATE SET
  drug_class = S.drug_class, is_anti_inflammatory = S.is_anti_inflammatory
WHEN NOT MATCHED THEN INSERT (drug_name, drug_class, is_anti_inflammatory)
  VALUES (S.drug_name, S.drug_class, S.is_anti_inflammatory);
