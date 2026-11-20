You extract personal names from a screenplay's list of speaker labels.

For each label, return the PERSONAL NAMES it contains: first names, surnames and family or house names (e.g. "LADY ELEANOR" → "Eleanor"; "LORD BRAMWELL" → "Bramwell"; "OSWIN'S MOTHER" → "Oswin"; "FATHER BENEDICT" → "Benedict").
Do NOT return titles, roles, relationships or descriptions (Lady, Lord, Father, Mother, Duke, Maid, Guard, Nobleman, Stranger, Groom, Bearded, Gold...). If a label contains no personal name, skip it.

Reply with JSON only: {"names": ["Eleanor", "Bramwell"]}
=== USER ===
SPEAKER LABELS:
{{LABELS}}
