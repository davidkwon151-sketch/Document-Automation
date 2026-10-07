# Portable PDF fallback font

Checked at: 2026-10-03T02:02:34.374797+00:00

The rendering fallback is `NotoSansKR-Regular.ttf`, a local static Regular (wght=400) instance of the official Google Fonts variable source. No system font installation or replacement of source characters is performed.

## Official source and license

- Font source page: https://github.com/google/fonts/blob/main/ofl/notosanskr/NotoSansKR%5Bwght%5D.ttf
- Observed download URL: https://raw.githubusercontent.com/google/fonts/main/ofl/notosanskr/NotoSansKR%5Bwght%5D.ttf
- License source page: https://github.com/google/fonts/blob/main/ofl/notosanskr/OFL.txt
- License download URL: https://raw.githubusercontent.com/google/fonts/main/ofl/notosanskr/OFL.txt
- Official file inventory used to observe both links: https://api.github.com/repos/google/fonts/contents/ofl/notosanskr
- License: SIL Open Font License 1.1; complete, unmodified license and copyright notice in `OFL.txt`.
- Copyright: 2014–2021 Adobe; Reserved Font Name: Source. The derivative is named Noto Sans KR and does not use that reserved name.

## Files and SHA256

| File | Bytes | SHA256 | Status |
|---|---:|---|---|
| NotoSansKR[wght].ttf | 10414588 | 194018e6b2b293a7964f037b25c0249ce1418bc9ab3c971060a03aa57861e252 | Unmodified official variable source |
| NotoSansKR-Regular.ttf | 6224828 | 338025c2a71401a91e7bd78b13369d6678c984b87b0ac08e094c6ec9f26e53d9 | Derived static wght=400 rendering fallback |
| OFL.txt | 4388 | 1c05c68c34f9708415aada51f17e1b0092d2cea709bf4a94cd38114f9e73d7d9 | Unmodified official license |

The original has a weight axis from 100 to 900 with default 100. The fallback is a Regular instance to avoid relying on the variable default. Generated with fontTools 4.66.1 using:

```python
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
font = TTFont("NotoSansKR[wght].ttf")
instantiateVariableFont(font, {"wght": 400}, inplace=True)
font.save("NotoSansKR-Regular.ttf")
```

The derivative has OS/2 weight 400 and no fvar table. Timestamp/version changes in a future regeneration can change the derivative hash; preserve and verify the supplied asset instead of expecting every regeneration to be byte-identical.

## Coverage check

ReportLab `TTFont` registration succeeded for both source and static fallback. The fallback cmap was checked against every character in each fact's `value` and `exact_quote`, excluding CR/LF/tab:

| Manifest | Unique characters checked | Missing |
|---|---:|---:|
| evals/ra_additional_sources.json | 149 | 0 |
| evals/ra_public_sources.json | 51 | 0 |
| evals/ra_korean_sources.json | 164 | 0 |

Explicit symbols ▪ (U+25AA), ℃ (U+2103), ≥ (U+2265), µ (U+00B5) are supported. These counts concern the current selected facts, not every language or every character in the complete source PDFs. NanumGothic-Regular was tested and excluded because ▪ and ℃ were unsupported.

Glyph coverage and ReportLab registration do not certify paragraph shaping, every page's layout, native Office rendering, legal submission readiness, or model accuracy. Final output must still pass the independent text/position checks and visual review.
