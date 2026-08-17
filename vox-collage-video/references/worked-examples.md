# Worked examples — narration in, visual decision out

Every gate in this skill enforces a **floor**. None of them can invent a good
idea. This file is the other half: twelve real decisions from V10/Itaewon, each
with the narration that prompted it, the reasoning, the frame that shipped, and
**the obvious answer that was rejected and why**.

Read this at step 2a, before writing `visualTransformation` for any scene.

The rejected column matters more than the chosen one. In almost every case the
rejected option is what an AI reaches for first, it passes every gate, and it
is the thing the viewer called "templated, repetitive" twice.

---

## The one question that decides everything

> **What relationship must the viewer WATCH FORM?**

Not "what is being talked about" — that produces an illustration of the topic,
which is filler. The relationship is the thing a still image cannot carry: a
size against a body, a force meeting a wall, a count reaching a number, a chain
that exists and then fails.

If the answer is "there isn't one, it's just mood", say so honestly and make it
a `background-photo` scene — but keep those under ~23% of the video and never
let them cluster at the end. See `baseline.json`.

---

## 1 · A measurement — S4

> "trăm người đè chồng chất lên nhau trong một con hẻm nhỏ"

| | |
|---|---|
| **Relationship** | 3,2 m is narrow *compared to a human body* — the number means nothing alone |
| **Chosen** | `diagram` — drawn alley cross-section, hatched masonry walls both sides, ten overlapping figures packed edge to edge, a dimension line labelled **3,2 MÉT**, a slope indicator |
| **Rejected** | A photo of a crowded alley. It shows *a* crowd in *an* alley; it does not show that the alley is 3,2 m, and 3,2 m is the entire point of the sentence. |
| **Also rejected** | Bars of varying height for the figures — it read as a bar chart. Fixed by packing the figures against the wall lines. |
| **Rule extracted** | **When narration states a dimension, the dimension must appear as a measured line against something the viewer knows the size of.** A photograph cannot do this. |

## 2 · A mechanism — S5

> "dẫu có kéo mạnh cỡ nào cũng không thể rút họ ra được"

| | |
|---|---|
| **Relationship** | Force applied → force rebounds. The failure has to happen on screen. |
| **Chosen** | `flow` — a 10×10 density grid locks solid, label **KHỐI NGƯỜI BỊ KHOÁ CHẶT**, then a thick arrow labelled **LỰC KÉO** travels in, meets a drawn wall, and stops |
| **Rejected** | A cutout of a rescue worker pulling. It shows someone trying; it does not show *why trying fails*. The narration's claim is about physics, not about effort. |
| **Rule extracted** | **A mechanism scene must show the attempt AND its failure as two moments in time.** A single frozen image of the attempt is a different, weaker claim. |

## 3 · A count — S7

> "158 người thiệt mạng"

| | |
|---|---|
| **Relationship** | 158 is a quantity the viewer should *feel*, not read |
| **Chosen** | `data` — 158 memorial dots filling in 12 rows across the full frame width while a counter ticks up; the 158th dot lands in orange |
| **Rejected** | A large number `158` set in type. It is legible and it is empty — the viewer reads a numeral and feels nothing. |
| **Rejected** | 158 dots at `perRow=20`. Correct count, but the block was only 384 px tall in a 1920 frame — an accurate figure floating in white space. **Correct data, wrong size, still a failed scene.** |
| **Rule extracted** | **A counted thing must be counted on screen, and the count must fill the frame.** Both halves are required. |

## 4 · A question left hanging — S11

> "vốn dĩ hoàn toàn có thể tránh được, vậy rốt cuộc tại sao nó lại xảy ra?"

| | |
|---|---|
| **Relationship** | Prevention *existed* and then *broke*. The viewer must see it intact first. |
| **Chosen** | `flow` — four chain links draw themselves down the frame (dự báo → giới hạn lối vào → điều phối → cứu hộ), then link 3 snaps and drops |
| **Rejected** | A question mark, or the word **TẠI SAO?** on paper. It restates the narration instead of answering the viewer's question with an image. |
| **Rejected** | Drawing the chain already broken. Then there was never a chain — and "it was entirely preventable" needs the prevention to have visibly existed. |
| **Rule extracted** | **To show that something failed, show it working first.** Order in time carries meaning that a final-state image throws away. |

## 5 · A correction — S15

> "Itaewon không phải là tên một con phố đơn thuần, mà là một khu vực…"

| | |
|---|---|
| **Relationship** | Wrong idea replaced by right idea |
| **Chosen** | Street sign photo → two orange strokes cross it out → **PHƯỜNG ITAEWON / QUẬN YONGSAN** appears below |
| **Rejected** | Going straight to the map. Correcting a misconception requires the misconception to be on screen; erasing something the viewer never saw is not a correction. |
| **Rule extracted** | **"Không phải X mà là Y" needs X on screen, visibly negated, before Y arrives.** |

## 6 · A density claim — S18

> "mật độ dày đặc của các quán bar, cửa hàng thời trang và nhà hàng quốc tế"

| | |
|---|---|
| **Relationship** | Not *what kind* of shops — *how many, how close* |
| **Chosen** | `diagram` — seven shopfronts drawn wall to wall, sign bands, windows, awnings, doorways, growing left to right, a dimension line **MỘT ĐOẠN PHỐ** underneath |
| **Rejected** | A cocktail glass + a clothes rack. This actually shipped in an earlier version and the user named it: it illustrates the *category*, not the *density*, and reads as stock filler. |
| **Rejected (v1 of the fix)** | Shopfront heights varying 0.62→1.0 of the band. At full frame that is a **bar chart**, whatever the component is called. |
| **Rule extracted** | **A claim about quantity or density must be shown as quantity, not as one representative example.** And: what makes a rectangle read as a building is its parts (sign, window, door, awning), never its height. |

## 7 · A place — S2 / S12 / S16

> "một khu vực rộng khoảng 1,37 km² nằm ở quận Yongsan"

| | |
|---|---|
| **Relationship** | Where it is, and how big — both are spatial |
| **Chosen** | `map` — real MapLibre raster, camera held still, orange pin + labelled badge, and the 1,37 km² footprint drawn as a circle at true ground scale |
| **Rejected** | An orange dot on blank paper. This is what the *first* V10 shipped, and it is the single worst pattern in this project's history: **a pin with nothing to pin it to is a label, not a map.** |
| **Rule extracted** | **When narration names a real place, show the real place.** And an area given in km² should be drawn at true scale — the metres-per-pixel conversion is four lines of arithmetic. |

## 8 · A cultural cause — S20

> "từ các bộ phim truyền hình Hàn Quốc nổi tiếng, như Tầng lớp Itaewon"

| | |
|---|---|
| **Relationship** | Something on a screen pulled people into a real street |
| **Chosen** | `mockup` — `DeviceMockup` draws the TV; the drama still is the screen content |
| **Rejected** | Cutting out the TV set from a photo. `rembg` shredded it, and it was the wrong instinct: the device is a *shape*, and shapes are cheaper and cleaner drawn than extracted. |
| **Rule extracted** | **Draw the container, source only the content.** Cutting out a phone, a TV, a frame or a document edge is fighting the mask for something SVG does better. |

## 9 · A turn in the story — S22

> "đằng sau sự phồn hoa đó là một lịch sử thăng trầm"

| | |
|---|---|
| **Relationship** | The bright present is pushed aside by an older, darker record |
| **Chosen** | `split` — the prosperous half physically encroached on by an archive-paper half |
| **Rejected** | Two images side by side, static. A split that does not *move* is a comparison; this sentence is a reversal, and a reversal needs one side to give ground. |
| **Rule extracted** | **`narrativeFunction: reversal` means something on screen must lose territory.** A static diptych under-serves it. |

## 10 · A mood beat that still has to do work — S24

> "khu vực này từng gắn liền với những ký ức đau thương của chiến tranh"

| | |
|---|---|
| **Relationship** | The poetic name is swallowed by something darker |
| **Chosen** | Scorched-paper photo at `tint 0.3`, the name 梨泰院 visible from frame 0, then a dark layer rising from the bottom to consume it as the punch lands |
| **Rejected (shipped once)** | The same photo at `tint 0.5` with a headline. Near-black, nothing sank, nothing happened. The `visualTransformation` field said "chìm xuống" and **nothing on screen was sinking** — the plan described a transformation the build never performed. |
| **Rule extracted** | **`visualTransformation` is a promise, not a label.** If nothing in the built scene changes state, the field is a lie and the scene is a mood plate with text on it. |

## 11 · A date far in the past — S25

> "Vào cuối thế kỷ 16, quân đội Nhật đã chiếm đóng khu vực này"

| | |
|---|---|
| **Relationship** | Time running backwards, landing on the same ground the viewer already saw from above |
| **Chosen** | Two languages stacked: aged-document photo (`wash="paper"`) as ground + timeline running back to TK 16 + a Korean gate cutout, then the same 1,37 km² footprint returns shaded as occupied |
| **Rejected** | The timeline alone on plain paper. Correct information, no atmosphere, and the frame's lower half empty. |
| **Rule extracted** | **Most strong scenes are two languages stacked, not one used well.** Measured on V10: 42% of scenes layer ≥2 asset roles, and the scenes that don't are the weakest in the video (S8, S20). |

## 12 · Two groups, eight years apart — S9 / S10

> "nhóm người thiệt mạng tại Itaewon lần này, lại chính là… các em học sinh cấp 3"

| | |
|---|---|
| **Relationship** | Two separate tragedies, one shared age |
| **Chosen** | S9 layers a crowd mass behind the victim group; S10 mirrors both groups across a dashed divider with a dimension line **CÁCH NHAU 8 NĂM** spanning them |
| **Rejected** | Two cutouts at 440 px on a 1080 frame with labels. Everything correct, everything small; the band 300→1250 was 45% empty. |
| **Rule extracted** | **A comparison must span the frame.** Two small things centred with white around them read as hesitancy, not as a comparison. |

---

## The rules, collected

Copy these into your head before step 2a:

1. Ask what relationship must **form**, not what is being talked about.
2. A stated dimension needs a measured line against a known-size object.
3. A mechanism needs the attempt **and** the failure, in that order.
4. A counted thing must be counted on screen, and the count must fill the frame.
5. To show something failed, show it working first.
6. "Không phải X mà là Y" needs X visibly negated on screen.
7. A density claim must be shown as density, never as one example of the category.
8. A named real place gets a real map; an area in km² gets drawn at true scale.
9. Draw the container, source only the content.
10. A reversal means something loses territory.
11. `visualTransformation` is a promise — if nothing changes state, it's a lie.
12. Two languages stacked beat one used well.
13. A comparison spans the frame.

Rules 2, 4, 7, 9 and 13 are not enforceable by any gate in this skill. They are
the part that has to be **judged**, and they are why this file exists.
