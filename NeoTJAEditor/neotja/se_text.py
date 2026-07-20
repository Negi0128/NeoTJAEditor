"""自動打音表記 (automatic SE text) - a faithful port of PeepoDrumKit's
`ChartCourse::RecalculateSENotes`.

Source of truth (read-only reference clone):
  PeepoDrumKit/src/peepo_drum_kit/chart_editor_widgets_game.cpp:436-550
    `ChartCourse::RecalculateSENotes(BranchType)` - the whole algorithm.
  PeepoDrumKit/src/peepo_drum_kit/chart_editor_widgets_game.cpp:408-434
    `ForEachNoteOnNoteLane` - what "a note" is and which tempo/scroll value
    each note carries (the one in effect *at that note's own beat*).
  PeepoDrumKit/src/peepo_drum_kit/chart.h:35-42
    `enum class NoteSEType` - the full output set.
  PeepoDrumKit/src/peepo_drum_kit/chart_editor_widgets_game.cpp:241-290
    `DrawGamePreviewNoteSEText` - where the label is drawn (see PLACEMENT).

================================ THE SPEC ================================

Terminology: "curr" is the note being classified. The algorithm keeps a
4-slot ring buffer over the time-sorted note list holding
    (prev, curr, next, n2nd)
i.e. exactly ONE note behind and TWO notes ahead (cpp:441-443). The list is
primed by consuming three notes before the first classification and drained
afterwards (cpp:536-549), so the first note sees prev=None and the last two
see next/n2nd=None.

"A note" here means every entry of the course's note list: don/ka (small and
big) AND the *head* of every drumroll/balloon/kusudama. Roll tails ('8') are
not notes. Long notes therefore take part in the spacing math even though
their own label is not a syllable.

--- Distances (cpp:448-474) ---
For a neighbour `other` of `curr`:
    time distance  td      = |other.time - curr.time|        (seconds)
    visual beats   vbd     = vbps_other * td                 (NMSCROLL)
    vbps_other             = |scroll_other| * bpm_other / 60
`vbps` = "visual beats per second": how many beats' worth of lane travel
happen per second at the *neighbour's* tempo and scroll, i.e. exactly the
on-screen gap you see when curr sits on the judgement circle. Note it is the
NEIGHBOUR's bpm/scroll that is used, never curr's.

The scroll of `next` is capped at 1.0 before use (`scrollNextCapped`,
cpp:463) so that a fast #SCROLL section cannot push a note's own label out
from under it; `prev`'s scroll is not capped.

SKIPPED ON PURPOSE - HBSCROLL / BMSCROLL:
PeepoDrumKit's `getVisualBeat` (cpp:448-454) has three branches keyed on the
neighbour's ScrollMethod:
    NMSCROLL : vbd = vbps_other * td                  <- implemented here
    HBSCROLL : vbd = |scroll_other| * |curr.beat - other.beat|
    BMSCROLL : vbd = |curr.beat - other.beat|
NeoTJAEditor's preview implements only the time-based NMSCROLL model (there
is no #HBSCROLL/#BMSCROLL handling anywhere in tja_analyzer.build_preview_
timeline, and no beat-space position for a note is even retained), so only
the NM branch is ported. For a chart that never uses #HBSCROLL/#BMSCROLL -
which is every chart NeoTJAEditor can currently render - the NM branch is
what PeepoDrumKit itself would take, so results are identical.

Missing neighbours use PeepoDrumKit's saturating F32Max sentinel: td and vbd
both become "effectively infinite". Because `F32Max + 1e-6 == F32Max` in
float, comparisons against the sentinel behave exactly like infinity, which
is what this port uses (see INF below) - this is load-bearing, not a
shortcut: it is what makes the LAST note of a chart come out as a long form.

--- Thresholds (cpp:487-494) ---
    timeEpsilon   = 1e-6 s          (Time::FromMS(1e-3))
    beatsEpsilon  = 4/192           (a 192nd note, in beats)

    denseToSparse = td_next  >= td_prev + timeEpsilon
        "the gap after curr is bigger than the gap before it" -> curr ends a
        run.
    sparseToDense = td_n2nd  <= td_next - timeEpsilon
        "the gap after next is smaller than the gap to next" -> curr starts a
        run.
    isLongAvoided = vbd_prev       <= 4/16 - beatsEpsilon    (<= 0.2291666..)
                 or vbd_next_capped <= 4/12 - beatsEpsilon   (<= 0.3125)
        The long (2-glyph) form is suppressed when the label would overlap
        the previous note's label or extend under the next note. 4/16 beats
        is a 16th note, 4/12 a 12th (8th triplet).
    isPrePause    = vbd_next_capped >= 4/8 + beatsEpsilon    (>= 0.5208333..)
        "there is at least an 8th note of empty lane after curr".

    LONG form  <=>  not isLongAvoided
                    and (denseToSparse or sparseToDense or isPrePause)
    SHORT form <=>  otherwise

Worked consequences (all verified in scratchpad/test_se_text.py):
  * 4th notes  (vbd 1.0)  -> isPrePause  -> ドン / カッ
  * 8th notes  (vbd 0.5)  -> nothing fires mid-run -> ド / カ,
    but the last of the run gets denseToSparse -> ドン / カッ
  * 16th notes (vbd 0.25) -> isLongAvoided via next (0.25 <= 0.3125) -> ド,
    last of the run: vbd_prev 0.25 is NOT <= 0.229 and next is far, so
    denseToSparse -> ドン
  * 24th/32nd (vbd <= 1/6) -> isLongAvoided via prev too, so even the last
    note of the run stays ド

--- 交互 (alternating) chain -> コ (cpp:477-516) ---
State: `alterChain` (list of notes), `isAlterChain` (bool, starts True),
`timeIntervalAlter`, `timeStartAlter`.

Per note, *before* the type switch (cpp:495-506), while isAlterChain:
  - curr is a SMALL DON and the chain is empty:
        start it - timeIntervalAlter = td_next, timeStartAlter = curr.time,
        append curr.
  - curr is a SMALL DON, |td_prev - timeIntervalAlter| < timeEpsilon and
    |timeStartAlter - curr.time| < 0.5 s + timeEpsilon:
        append curr (evenly spaced, and the whole chain stays inside half a
        second).
  - otherwise: isAlterChain = False, chain cleared.
Big dons, ka of any size, rolls and balloons therefore all break the chain.

Then (cpp:507-516) if denseToSparse or sparseToDense:
  - if denseToSparse and isAlterChain and not isLongAvoided(curr) and
    len(alterChain) is ODD and the chain spans < 0.5 s + eps:
        every ODD INDEX of the chain (the 2nd, 4th, ... note) becomes コ.
        Because the length is odd, curr itself is at an even index and keeps
        its own form - so an evenly-spaced run of 5 small dons ending in a
        gap renders ド コ ド コ ドン, the classic ドコドコドン.
  - chain cleared; isAlterChain = sparseToDense.

--- Output syllables (cpp:518-531, chart.h:35-42) ---
    small don   -> Don (ドン) if LONG else Do (ド)
    small ka    -> Katsu (カッ) if LONG else Ka (カ)
    big don     -> DonBig (ドン)      always long, density is irrelevant
    big ka      -> KatsuBig (カッ)    always long
    big don/両手 -> DonHand   (TJAP2fPC 'A' - NeoTJAEditor has no such note)
    big ka/両手  -> KatsuHand (TJAP2fPC 'B' - ditto)
    drumroll    -> Drumroll (れんだ) / DrumrollBig
    balloon     -> Balloon (ふうせん) / BalloonSpecial (くすだま)
    anything else -> NoteSEType::Count, i.e. no label at all
The コ override above is applied on top of this for small dons.

--- #SENOTECHANGE ---
Deliberately NOT supported, per the user's request. This costs nothing in
fidelity: PeepoDrumKit parses the command (file_format_tja.cpp:854-858) but
never applies it - it is dropped on the way into the chart model and is a
`// TODO: DEPRECATED (?)` no-op on the way back out
(file_format_tja.cpp:1399-1402). `RecalculateSENotes` never reads any
per-note SE override, so a chart that lacks #SENOTECHANGE and a chart that
has it produce byte-identical auto-detection results in PeepoDrumKit.

--- PLACEMENT (cpp:241-245, chart_editor_theme.h:26-37) ---
`DrawGamePreviewNoteSEText` shifts the label from the lane's content centre
down to its *footer* centre:
    contentToFooterOffsetY = FooterCenterY() - ContentCenterY()
                           = (12+195+6+19.5) - (12+97.5) = 123 px
i.e. the syllable is drawn horizontally centred on the note but vertically
in a dedicated strip *below* the note lane (Content=195, Footer=39, so the
footer is 20% of the content height). chart_preview_widget.py reproduces
this with SE_FOOTER_HEIGHT under the lane band - see the comment there.

========================================================================

Timing note: the analyzer works in Decimal, but this module deliberately
works in float seconds. PeepoDrumKit's `Time` is a plain f64 second count
and every threshold here (1e-6 s, 4/192 beats) sits ~6 orders of magnitude
above float64 round-off for any realistic chart, so float is both faithful
and required for the F32Max/infinity saturation described above to behave
the way the C++ does.
"""

# Note kinds fed to compute_se_types(). These mirror PeepoDrumKit's NoteType
# (chart.h:12-33) for exactly the subset TJA/NeoTJAEditor can express.
KIND_DON = "don"
KIND_KA = "ka"
KIND_DON_BIG = "don_big"
KIND_KA_BIG = "ka_big"
KIND_DRUMROLL = "drumroll"
KIND_DRUMROLL_BIG = "drumroll_big"
KIND_BALLOON = "balloon"
KIND_BALLOON_SPECIAL = "balloon_special"

# TJA note characters -> kind, for the '1'-'4' notes of build_preview_timeline.
CHAR_KIND = {"1": KIND_DON, "2": KIND_KA, "3": KIND_DON_BIG, "4": KIND_KA_BIG}

# NoteSEType (chart.h:35-42) -> the syllable PeepoDrumKit draws for it. The
# C++ blits per-type sprites (Game_NoteTxt_*); this port draws text, so the
# table holds the kana those sprites show. DonHand/KatsuHand are the
# TJAP2fPC two-handed big notes, which TJA as parsed here cannot produce -
# kept only so the table matches the enum one-for-one.
SE_LABELS = {
    "Do": "ド",
    "Ko": "コ",
    "Don": "ドン",
    "DonBig": "ドン",
    "DonHand": "ドン",
    "Ka": "カ",
    "Katsu": "カッ",
    "KatsuBig": "カッ",
    "KatsuHand": "カッ",
    "Drumroll": "れんだ",
    "DrumrollBig": "れんだ",
    "Balloon": "ふうせん",
    "BalloonSpecial": "くすだま",
}

# PeepoDrumKit's F32Max "no such neighbour" sentinel. Float saturation makes
# `F32Max +- timeEpsilon == F32Max`, so infinity reproduces every comparison
# the C++ performs against it exactly (see the module docstring).
INF = float("inf")

TIME_EPSILON = 1e-6          # Time::FromMS(1e-3)               cpp:487
BEATS_EPSILON = 4 / 192.0    #                                  cpp:490
LONG_AVOID_PREV = 4 / 16.0 - BEATS_EPSILON   # cpp:491
LONG_AVOID_NEXT = 4 / 12.0 - BEATS_EPSILON   # cpp:492
PRE_PAUSE = 4 / 8.0 + BEATS_EPSILON          # cpp:493
ALTER_CHAIN_MAX_SPAN = 0.5 + TIME_EPSILON    # cpp:500/508


def _visual_beat(other, time_distance):
    """NMSCROLL branch of PeepoDrumKit's getVisualBeat (cpp:448-454).

    `other` is (time, kind, bpm, scroll_already_processed) or None. The
    HBSCROLL/BMSCROLL branches are intentionally absent - see the module
    docstring."""
    if other is None:
        return INF
    _t, _kind, bpm, scroll = other
    return (scroll * bpm / 60.0) * time_distance


def compute_se_types(sequence):
    """PeepoDrumKit's RecalculateSENotes (cpp:436-550), one output per input.

    `sequence` is the course's note list in time order:
        [(time_seconds: float, kind: str, bpm: float, scroll: float), ...]
    where `kind` is one of the KIND_* constants and roll/balloon *heads*
    count as notes exactly like they do in the C++ (ForEachNoteOnNoteLane,
    cpp:409-434). Returns a list of NoteSEType names (keys of SE_LABELS), or
    None for a note PeepoDrumKit would leave as NoteSEType::Count.
    """
    n = len(sequence)
    out = [None] * n
    if n == 0:
        return out

    def at(i):
        # prev/curr/next/n2nd, with the out-of-range slots left empty exactly
        # like the zero-initialized ring buffer entries (cpp:441).
        return sequence[i] if 0 <= i < n else None

    alter_chain = []          # indices into `sequence`
    is_alter_chain = True
    time_interval_alter = 0.0
    time_start_alter = 0.0

    for i in range(n):
        prev, curr, nxt, n2nd = at(i - 1), sequence[i], at(i + 1), at(i + 2)
        curr_time, curr_kind = curr[0], curr[1]

        # --- distances (getNoteDistance, cpp:456-474) ------------------
        td_prev = INF if prev is None else (curr_time - prev[0])
        td_next = INF if nxt is None else (nxt[0] - curr_time)
        td_n2nd = INF if (n2nd is None or nxt is None) else (n2nd[0] - nxt[0])
        # `next`'s scroll is capped at 1.0, `prev`'s is not (cpp:462-463).
        prev_v = None if prev is None else (prev[0], prev[1], prev[2], abs(prev[3]))
        next_v = None if nxt is None else (nxt[0], nxt[1], nxt[2], min(1.0, abs(nxt[3])))
        vbd_prev = _visual_beat(prev_v, td_prev)
        vbd_next_capped = _visual_beat(next_v, td_next)

        # --- form classification (cpp:486-494) -------------------------
        dense_to_sparse = td_next >= td_prev + TIME_EPSILON
        sparse_to_dense = td_n2nd <= td_next - TIME_EPSILON
        is_long_avoided = (vbd_prev <= LONG_AVOID_PREV
                           or vbd_next_capped <= LONG_AVOID_NEXT)
        is_pre_pause = vbd_next_capped >= PRE_PAUSE
        is_long = (not is_long_avoided
                   and (dense_to_sparse or sparse_to_dense or is_pre_pause))

        # --- 交互 chain bookkeeping (cpp:495-506) ----------------------
        if is_alter_chain:
            if curr_kind == KIND_DON and not alter_chain:
                time_interval_alter = td_next
                time_start_alter = curr_time
                alter_chain.append(i)
            elif (curr_kind == KIND_DON
                  and abs(td_prev - time_interval_alter) < TIME_EPSILON
                  and abs(time_start_alter - curr_time) < ALTER_CHAIN_MAX_SPAN):
                alter_chain.append(i)
            else:
                is_alter_chain = False
                alter_chain = []

        # --- コ assignment + chain reset (cpp:507-516) -----------------
        if dense_to_sparse or sparse_to_dense:
            if (dense_to_sparse and is_alter_chain and not is_long_avoided
                    and len(alter_chain) % 2 != 0
                    and abs(time_start_alter - curr_time) < ALTER_CHAIN_MAX_SPAN):
                for ia, note_idx in enumerate(alter_chain):
                    if ia % 2 == 1:
                        out[note_idx] = "Ko"
            alter_chain = []
            is_alter_chain = sparse_to_dense

        # --- output syllable (cpp:518-531) -----------------------------
        if curr_kind == KIND_DON:
            out[i] = "Don" if is_long else "Do"
        elif curr_kind == KIND_KA:
            out[i] = "Katsu" if is_long else "Ka"
        elif curr_kind == KIND_DON_BIG:
            out[i] = "DonBig"
        elif curr_kind == KIND_KA_BIG:
            out[i] = "KatsuBig"
        elif curr_kind == KIND_DRUMROLL:
            out[i] = "Drumroll"
        elif curr_kind == KIND_DRUMROLL_BIG:
            out[i] = "DrumrollBig"
        elif curr_kind == KIND_BALLOON:
            out[i] = "Balloon"
        elif curr_kind == KIND_BALLOON_SPECIAL:
            out[i] = "BalloonSpecial"
        else:
            out[i] = None

    return out


def compute_note_se_labels(notes, rolls=(), balloons=(), kusudamas=()):
    """SE syllable per entry of `notes`, in `notes` order.

    `notes` is build_preview_timeline's [(time, char, bpm, scroll), ...]
    ('1'-'4' only). Roll/balloon/kusudama *heads* are merged into the
    sequence for the spacing math - PeepoDrumKit keeps them in the same note
    list (cpp:409-434), so a note sitting right before a drumroll must see
    that roll as its `next` or the run-end detection would be wrong - but
    they get no returned label: NeoTJAEditor draws those spans as capsule
    bars, not as stretched れんだ/ふうせん sprites (cpp:266-285), so their
    syllables have nowhere to go.

    Returns a list of strings (or None) the same length as `notes`.
    """
    merged = []  # (time, kind, bpm, scroll, index_into_notes or -1)
    for i, (t, c, bpm, scroll) in enumerate(notes):
        merged.append((float(t), CHAR_KIND.get(c), float(bpm), float(scroll), i))
    for r in rolls:
        # (start, end, char, bpm, scroll, hits) - only the head is a note.
        kind = KIND_DRUMROLL_BIG if r[2] == "6" else KIND_DRUMROLL
        merged.append((float(r[0]), kind, float(r[3]), float(r[4]), -1))
    for b in balloons:
        # (start, end, bpm, scroll, hits)
        merged.append((float(b[0]), KIND_BALLOON, float(b[2]), float(b[3]), -1))
    for k in kusudamas:
        merged.append((float(k[0]), KIND_BALLOON_SPECIAL, float(k[2]), float(k[3]), -1))
    # Stable sort by time only, so notes that share a timestamp keep the
    # order they were emitted in (the C++ list is beat-sorted the same way).
    merged.sort(key=lambda e: e[0])

    types = compute_se_types([(e[0], e[1], e[2], e[3]) for e in merged])
    labels = [None] * len(notes)
    for se_type, entry in zip(types, merged):
        if entry[4] >= 0:
            labels[entry[4]] = SE_LABELS.get(se_type) if se_type else None
    return labels
