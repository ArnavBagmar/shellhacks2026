"""Step 20 -- spoken questions about the surroundings, answered by a VLM.

"Hey Cane, is there a bathroom around?" arrives as a ``query`` frame; the
laptop takes the freshest X4 frame, dewarps it into the six 60-degree crops
the bridge already builds (plus the phone's own front-camera photo, when it
sent one), asks Gemini, and returns one ``answer`` frame. Tier 3: 1-3 s,
outside the safety path, and never allowed to hang -- every failure becomes
``status: unavailable``.
"""
