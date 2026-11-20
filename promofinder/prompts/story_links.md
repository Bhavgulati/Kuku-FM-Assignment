You are a story editor. Below is the beat map of a whole serialized drama, in story order. Each line gives the beat ID (episode.number), its kind, its length, a summary, and what a viewer needs to know first.

Find up to {{L}} SETUP → PAYOFF links. A link joins an earlier beat that plants something with a later beat, usually in a later episode, where it pays off: the secret comes out, the threat lands, the promise is kept or broken, the tables turn. Only link two beats when seeing the setup first makes the payoff hit much harder for someone who has never seen the show. List the best links first.

Also group the beats into up to 8 storylines ("threads"). Give each a short name and its beat IDs in story order.

Use only beat IDs from the map. Reply with JSON only, in this shape:
{"links": [{"setup": "E02.1", "payoff": "E17.3", "why": "one sentence"}], "threads": [{"name": "...", "beats": ["E02.1", "E09.2"]}]}
=== USER ===
BEAT MAP:
{{BEATS}}
