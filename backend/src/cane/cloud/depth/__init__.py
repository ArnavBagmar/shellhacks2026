"""The depth head: Depth Anything V2 Small on the three forward crops (step 22).

`POST /infer` already answers with detections. This package adds one
`GapProfile` per forward crop -- 16 column means of **raw** relative depth,
vertically band-filtered, plus the ground-plane fit that step 23 needs to turn
a ranking into metres (docs/shepherd-steering-plan.md Sec 2.1).

Three rules from the plan shape every module here:

1. **Never normalise per frame** (Sec 1.1). Depth Anything V2 returns relative
   inverse depth on no fixed scale; min-max normalising a frame makes an empty
   corridor score identically to a wall, because the nearest thing in frame
   always becomes 1.0. `benchmarks.depth_model.normalize_depth` is that
   function and it appears nowhere below.
2. **Absence, never a fake** (Sec 4.2, step 8 S1). A model that will not load,
   a crop the model chokes on, a map full of NaN -- each costs the frame its
   profile and costs the detections nothing. Sixteen zeros would read
   downstream as a wall at the wearer's feet.
3. **No clock** (Sec 4.4). `perf_counter` measures durations; the capture
   stamp is echoed. Proven statically by `tests/cloud/test_no_clock_reads.py`.
"""
