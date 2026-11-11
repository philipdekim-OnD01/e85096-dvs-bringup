# S5KRC1S D-PHY PLL: Minsu + Karim review (2026-11-10)

> Engineering handoff for Claude / Codex. Evidence-based guidance, not a validated production setting.

## Status / decision
- On Exynos850 E850-96, changing sensor register `0x30A6` from `0x8D` to `0x75` reduced observed MIPI CSI CRC errors from ~4,800/s to zero during a **3-minute** test. This is a useful observation, **not** evidence of long-term link stability or a known lane rate.
- **Do not promote 0x75 to a production/default setting.** Preserve the current working configuration and logs for A/B comparison.
- Preferred candidate to test: **0x30A5/0x30A6/0x30A7 = 00/8B/B0**, nominal **2244 Mbps/lane**, with E850 receiver configuration matched. Alternative **00/8B/F8** (2298 Mbps/lane), reportedly worked on E850 previously.
- No E850 long-duration validation of the proposed candidate is claimed here.

## Evidence from collaborators' email replies
### Minsu
- The three registers form a **19-bit PLL value**: `P=[18:13]` (6 bits), `M=[12:3]` (10 bits), `S=[2:0]` (3 bits).
- Formula for his tested S=0 cases: `lane_rate_Mbps = 24 * M / P`.
- Tested on Jetson with receiver rate matched: `00/8D/00` = 2496, `00/8B/F8` = 2298, `00/8B/B0` = 2244, `00/86/88` = 1254 Mbps/lane.
- Jetson at `00/8B/B0` (P=4, M=374, S=0), DT `pix_clk_hz=1122 MHz`: **64.37 hours RAW2 recording, 0 CRC errors, 0 lost buffers**. Jetson at 2298 Mbps: 2 CRC errors in ~5 hours.
- Below 2220 Mbps with RAW2 1600 fps setfile, truncated frames occurred **without CRC errors**. Frame length/completeness is a separate acceptance criterion.
- Default `00/8D/00` (2496 Mbps) may depend on short sensor-to-FPGA interconnect; not established as reliable on E850.

### Karim (2026-11-10)
- If the other registers remain `00`, `00/75/00` decodes to **P=3, M=672, S=0**, yielding **5376 Mbps by formula**, outside the reported PLL VCO range of 1250–2500 MHz. **Actual transmitted lane rate is unknown.**
- The top 3 bits of `0x30A6` contribute to P; its lower 5 bits contribute to M. Changing that byte is not simply changing M.
- Karim adds a **hypothesized** S divider: `lane_rate_Mbps = 24 * M / (P * 2^S)`. This comes from experiments (including CX3 setfile), **not confirmed by the sensor datasheet**. Minsu's listed settings had S=0.
- Read back all three live sensor registers; optionally read PLL lock/status if such a register exists. Readback confirms programmed values, **not actual PLL frequency**.
- If proper high-speed instrumentation is available, measure MIPI clock lane frequency directly.
- Check CRC, frame length/content and buffer loss over **several hours**, not minutes. Zero CRC alone does not prove complete frames.

## Claude implementation checklist (do not assume any step is complete)
1. Locate the existing S5KRC1S setfile, I2C read/write path, CSI receiver clock/lane configuration, and CRC/frame/buffer counters. Record paths and current behavior before editing.
2. Add non-destructive readback logging for `0x30A5`, `0x30A6`, `0x30A7` **after sensor startup**, with full 19-bit decode: `word=(A5<<16)|(A6<<8)|A7`; `P=(word>>13)&0x3f`; `M=(word>>3)&0x3ff`; `S=word&7`. Handle P=0. Label the S divider formula as experimental.
3. Keep existing `0x75` behavior available only as a clearly labeled experimental configuration; do not silently change known working behavior. Add named, selectable PLL profiles `experimental_00_75_00`, `recommended_00_8b_b0`, `alternative_00_8b_f8` if architecture permits.
4. Match E850 CSI receiver timing/rate to selected sensor profile using **E850-specific** driver/clock configuration. **Do not blindly copy Jetson DT pix_clk_hz=1122 MHz**.
5. Log per-run: profile, live PLL readback, setfile, RAW2 mode, lane count, configured receiver rate, elapsed time, CRC count/rate, frame/buffer count, expected vs actual frame bytes, truncated frames, timestamp/event continuity and representative decoded data.
6. Run controlled A/B tests with all other variables fixed: 10 min -> 1 h -> 24 h when feasible. Include restarts and repeat runs. Capture logs and explicit pass/fail criteria.
7. Check datasheet/source for actual VCO limits, S behavior, lock register and clocking semantics before asserting hardware-level certainty. If no source, mark UNKNOWN.
8. Do not claim zero lost events solely from zero CRC. Keep raw captures or sufficient diagnostic samples for independent review.
9. Document actual findings separately from hypotheses and collaborator-reported Jetson results. Update README only after E850 measurements.

## Suggested validation matrix
| Profile | A5/A6/A7 | Nominal lane rate | Evidence | Status |
|---|---|---:|---|---|
| Existing experiment | 00/75/00 (conditional: verify readback) | 5376 Mbps calculated; **actual unknown** | E850 3 min CRC=0 | NOT validated |
| Candidate | 00/8B/B0 | 2244 Mbps | Jetson 64.37 h, CRC=0, lost buffers=0 | E850 test pending |
| Alternative | 00/8B/F8 | 2298 Mbps | Jetson ~5 h 2 CRC; reportedly worked on E850 | E850 retest pending |
| Default reference | 00/8D/00 | 2496 Mbps | E850 ~4800 CRC/s before change | Reference only |

## Safety / scope
- Do not modify sensor PLL registers on a running stream without a controlled stop/reconfigure/start sequence and appropriate CSI receiver synchronization.
- Changes should be isolated, reversible, reviewed, and validated against existing E850 boot/driver/decoder operation.
- Source: Minsu's and Karim's November 2026 email replies to the E850 DVS porting report. Their observations are quoted as collaborator evidence, not independently reproduced on E850.
