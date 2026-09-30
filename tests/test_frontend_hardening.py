"""Static guards for the frontend hardening brief, section 3.

These are the checks that can be made by reading the source. The rendered checks
- no `{{` on any page, no console errors, Indian digit grouping - need a browser
against a live stack and belong to section 4.

Each test says which numbered item it holds, because the point of a guard is
that someone who breaks it can find out why it exists.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

WEB = pathlib.Path(__file__).resolve().parents[1] / "web" / "src"
I18N = WEB / "i18n"

# Only this module may name a tile server.
TILE_CONFIG_MODULE = WEB / "lib" / "tiles.ts"


def source_files() -> list[pathlib.Path]:
    return sorted(
        p for p in WEB.rglob("*")
        if p.suffix in {".ts", ".tsx"} and p.is_file()
    )


def strip_comments(text: str) -> str:
    """Remove `//` and `/* */` comments, leaving string literals intact.

    Necessary, not fastidious. These guards assert that certain strings do *not*
    appear in the source, and the code that was fixed is described in comments
    right next to the fix - so a naive scan finds `'A'`, `overflow-x-auto` and
    `cartocdn` in the very comments explaining why they were removed, and the
    guard fails on its own documentation. All four of these tests did that on
    their first run.

    A state machine rather than a regex because `https://` inside a string is
    not a comment, and a regex that gets that wrong would silently stop scanning
    half of `lib/tiles.ts`.
    """
    out = []
    i = 0
    n = len(text)
    quote: str | None = None
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""

        if quote:
            out.append(ch)
            if ch == "\\":
                if i + 1 < n:
                    out.append(nxt)
                    i += 2
                    continue
            elif ch == quote:
                quote = None
            i += 1
            continue

        if ch in "\"'`":
            quote = ch
            out.append(ch)
            i += 1
            continue

        if ch == "/" and nxt == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue

        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def code_of(path: pathlib.Path) -> str:
    """A source file with its comments removed."""
    return strip_comments(path.read_text(encoding="utf-8"))


def load(lang: str) -> dict:
    return json.loads((I18N / f"{lang}.json").read_text(encoding="utf-8"))


def flatten(obj: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in obj.items():
        if isinstance(value, dict):
            out.update(flatten(value, f"{prefix}{key}."))
        else:
            out[f"{prefix}{key}"] = value
    return out


# ---------------------------------------------------------------------------
# Item 10: the tile source
# ---------------------------------------------------------------------------


def test_no_source_file_mentions_cartocdn():
    """Item 10. CARTO's keyless endpoint now serves "API key required"
    watermarks, and the URL was hardcoded in the map page."""
    offenders = [
        str(p.relative_to(WEB)) for p in source_files()
        if "cartocdn" in code_of(p)
    ]
    assert offenders == [], (
        f"cartocdn appears in {offenders}. The tile source belongs in "
        "lib/tiles.ts and comes from VITE_TILE_URL; see DECISIONS.md D-007."
    )


def test_no_tile_url_outside_the_config_module():
    """Item 10, the general form. Naming any tile server outside `lib/tiles.ts`
    puts the product back to depending on a third party's free tier in a place
    nobody will look when it breaks."""
    # A tile template is recognisable: a URL carrying Leaflet's {z}/{x}/{y}
    # placeholders.
    pattern = re.compile(r"https?://[^\s'\"]*\{[zxy]\}")
    offenders = []
    for path in source_files():
        if path == TILE_CONFIG_MODULE:
            continue
        for match in pattern.finditer(code_of(path)):
            offenders.append(f"{path.relative_to(WEB)}: {match.group(0)}")
    assert offenders == [], (
        "tile URL(s) outside lib/tiles.ts:\n  " + "\n  ".join(offenders)
    )


def test_the_tile_config_defaults_to_a_keyless_provider():
    """The default must need no key, or a fresh checkout has no base map."""
    text = TILE_CONFIG_MODULE.read_text(encoding="utf-8")
    assert "tile.openstreetmap.org" in text
    assert "openstreetmap.org/copyright" in text, (
        "the default attribution must link to the copyright page, which is what "
        "OpenStreetMap's terms ask for"
    )
    for key in ("VITE_TILE_URL", "VITE_TILE_ATTRIBUTION",
                "VITE_TILE_MAX_ZOOM", "VITE_TILE_SUBDOMAINS"):
        assert key in text, f"{key} is not read by lib/tiles.ts"


def test_all_four_tile_variables_are_documented_in_env_example():
    example = (pathlib.Path(__file__).resolve().parents[1] / ".env.example").read_text(
        encoding="utf-8"
    )
    for key in ("VITE_TILE_URL", "VITE_TILE_ATTRIBUTION",
                "VITE_TILE_MAX_ZOOM", "VITE_TILE_SUBDOMAINS"):
        assert key in example, f"{key} is not in .env.example"
    # The "for later" keyed examples the brief asks for.
    assert "maptiler" in example.lower()
    assert "thunderforest" in example.lower()


def test_tile_failure_is_handled():
    """Item 10. A withdrawn provider must not take the booth data with it."""
    tiles = TILE_CONFIG_MODULE.read_text(encoding="utf-8")
    base = (WEB / "components" / "BaseTiles.tsx").read_text(encoding="utf-8")
    assert "TILE_FAILURE_THRESHOLD" in tiles
    assert "tileerror" in base, "nothing listens for Leaflet's tileerror"
    map_page = (WEB / "pages" / "MapExplorer.tsx").read_text(encoding="utf-8")
    assert "map.tilesUnavailable" in map_page, "no notice is shown on tile failure"
    assert "common.dismiss" in map_page, "the notice cannot be dismissed"


# ---------------------------------------------------------------------------
# Item 1 and 6: interpolation and plurals
# ---------------------------------------------------------------------------


def _interpolated_keys(lang: str) -> dict[str, set[str]]:
    """Every key whose value has `{{placeholders}}`, and their names."""
    out = {}
    for key, value in flatten(load(lang)).items():
        names = set(re.findall(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", str(value)))
        if names:
            out[key] = names
    return out


def test_every_interpolated_key_is_called_with_its_values():
    """Item 1, as close as a static check can get.

    The bug was `t('overview.daysLeft')` against the value
    `"{{count}} days left"`, which rendered the literal text
    `157 {{count}} days left` on the first screen of the product. The rendered
    check belongs to section 4; this one finds the same mistake without a
    browser, by pairing each key that needs values with the call that uses it.
    """
    interpolated = _interpolated_keys("en")
    sources = {p: code_of(p) for p in source_files()}
    failures = []

    for key, names in interpolated.items():
        # i18next selects `foo_one` / `foo_other` from a call to `foo`, so the
        # call site names the base key.
        base = re.sub(r"_(zero|one|two|few|many|other)$", "", key)
        for path, text in sources.items():
            for match in re.finditer(
                r"""t\(\s*['"]""" + re.escape(base) + r"""['"]\s*(,|\))""", text
            ):
                if match.group(1) == ")":
                    failures.append(
                        f"{path.relative_to(WEB)}: t('{base}') passes no values, "
                        f"but the string needs {sorted(names)}"
                    )

    assert failures == [], "\n  ".join(["interpolation without values:"] + failures)


def test_plural_keys_exist_in_pairs_in_both_languages():
    """Item 6. A `_one` without an `_other` falls back to the key name, which
    renders as `health.someMissing` on the page."""
    for lang in ("en", "hi"):
        keys = set(flatten(load(lang)))
        for key in sorted(keys):
            if key.endswith("_one"):
                assert key[: -len("_one")] + "_other" in keys, (
                    f"{lang}: {key} has no _other form"
                )
            if key.endswith("_other"):
                assert key[: -len("_other")] + "_one" in keys, (
                    f"{lang}: {key} has no _one form"
                )


def test_plural_keys_use_count_as_the_selector():
    """i18next selects the plural form from `count` specifically. A string that
    pluralises on `{{n}}` would always take the `_other` branch."""
    for lang in ("en", "hi"):
        for key, value in flatten(load(lang)).items():
            if key.endswith(("_one", "_other")):
                assert "{{count}}" in value or "{{formatted}}" in value, (
                    f"{lang}: {key} is a plural form but interpolates neither "
                    f"count nor formatted: {value!r}"
                )


def test_no_pluralised_key_is_still_called_with_n():
    """The call sites had to change from `{ n: ... }` to `{ count: ... }` when
    these keys were pluralised. A missed one silently loses the number."""
    plural_bases = {
        re.sub(r"_(one|other)$", "", k)
        for k in flatten(load("en"))
        if k.endswith(("_one", "_other"))
    }
    failures = []
    for path in source_files():
        text = code_of(path)
        for base in plural_bases:
            for match in re.finditer(
                re.escape(f"t('{base}'") + r"\s*,\s*\{([^}]*)\}", text
            ):
                if re.search(r"\bn\s*:", match.group(1)):
                    failures.append(f"{path.relative_to(WEB)}: t('{base}', {{ n: ... }})")
    assert failures == [], "\n  ".join(["plural key called with n, not count:"] + failures)


def test_the_two_languages_have_identical_key_sets():
    """A key present in one language only falls back silently, so an English
    string appears mid-Hindi page and nobody is told."""
    en = set(flatten(load("en")))
    hi = set(flatten(load("hi")))
    assert en - hi == set(), f"missing from hi.json: {sorted(en - hi)}"
    assert hi - en == set(), f"missing from en.json: {sorted(hi - en)}"


# ---------------------------------------------------------------------------
# Items 2, 3, 5, 7, 9, 14
# ---------------------------------------------------------------------------


def test_dates_do_not_default_to_one_language():
    """Item 2. `dateShort(value, lang = 'hi')` rendered Devanagari dates on
    English pages for every caller that omitted the argument, and two of five
    did."""
    text = code_of(WEB / "lib" / "format.ts")
    assert "lang = 'hi'" not in text, "dateShort still defaults to Hindi"
    assert "i18n.language" in text, (
        "dateShort should fall back to the live UI language, so a caller that "
        "does not care still gets the right answer"
    )


def test_an_unknown_source_page_is_not_rendered_as_a_value():
    """Item 3. `p?` is the absence of a page number dressed as one, and it was
    a link, inviting a reader to check a page that was never recorded."""
    text = code_of(WEB / "components" / "Provenance.tsx")
    assert "'?'" not in text and '"?"' not in text, "SourceLink still renders a '?'"
    assert "prov.pageUnknown" in text
    assert "isFixtureMode" in text, (
        "fixture provenance must be labelled as fixture, not linked as if the "
        "document were real"
    )
    assert "prov.fixtureLabel" in text


def test_no_card_scrolls_sideways_to_show_a_command():
    """Item 5. The data-health cards put a horizontal scrollbar inside a grid
    cell - the least reachable place to put one."""
    text = code_of(WEB / "components" / "Provenance.tsx")
    assert "overflow-x-auto" not in text, (
        "a horizontal scrollbar remains inside a card"
    )
    # Truncate, not wrap. Wrapping obeyed the no-scrollbar rule but broke
    # commands mid-token, and a path split across two lines reads as two
    # different paths - worse than a visible ellipsis. The full text is in the
    # tooltip and one click from the clipboard.
    assert "truncate" in text, "the command neither wraps nor truncates"
    assert "whitespace-pre-wrap" not in text, (
        "the command wraps again; it should be one line with an ellipsis"
    )
    assert "title={command}" in text, "the full command is not in a tooltip"
    assert "CopyButton" in text, "a command shown to be run should be copyable"


def test_the_header_names_the_platform_not_one_constituency():
    """Item 7. The product serves six constituencies; which one is in view is
    the switcher's job, and two places saying it meant one was always about to
    disagree with the other."""
    text = code_of(WEB / "components" / "Layout.tsx")
    assert "app.platform" in text
    assert "app.constituency" not in text, (
        "the header still names a constituency beside the platform name"
    )
    for lang in ("en", "hi"):
        platform = flatten(load(lang))["app.platform"]
        assert "Giridih" not in platform and "गिरिडीह" not in platform, (
            f"{lang}: app.platform still names a constituency: {platform!r}"
        )


def test_the_navigation_is_grouped_and_keyboard_reachable():
    """Item 8. Seventeen flat links wrapped the header onto a second row."""
    layout = (WEB / "components" / "Layout.tsx").read_text(encoding="utf-8")
    menu = (WEB / "components" / "NavMenu.tsx").read_text(encoding="utf-8")

    groups = re.findall(r"key: 'group([A-Za-z]+)'", layout)
    assert 4 <= len(groups) <= 6, (
        f"expected 5 or 6 groups, found {len(groups)}: {groups}"
    )
    assert "aria-expanded" in menu, "the menu button does not report its state"
    assert "aria-controls" in menu
    assert "Escape" in menu, "Escape must close the menu"
    assert "aria-label={t('nav.label')}" in layout, "the nav landmark is unnamed"


def test_the_theme_button_has_a_translated_accessible_name():
    """Item 9. The glyph was 'A', which abbreviates nothing, and the accessible
    name was an untranslated state rather than an action."""
    text = code_of(WEB / "components" / "ThemeToggle.tsx")
    assert "'A'" not in text, "the meaningless 'A' glyph is still there"
    assert "`Theme: ${theme}`" not in text, "the accessible name is still hardcoded English"
    assert "theme.switchTo" in text, "the name should say what pressing it does"


@pytest.mark.parametrize("lang", ["en", "hi"])
def test_filter_labels_are_whole_strings_not_concatenations(lang):
    """Item 14. `{t('common.all')} {t('common.block')}` produced "All Block".
    Two fragments glued together cannot be made grammatical in two languages at
    once, which is exactly why it must be one key."""
    keys = flatten(load(lang))
    assert "common.allBlocks" in keys
    assert "common.allAreas" in keys
    map_page = code_of(WEB / "pages" / "MapExplorer.tsx")
    assert "{t('common.all')} {t('common.block')}" not in map_page
    assert "{t('common.all')} {t('common.area')}" not in map_page
    if lang == "en":
        assert keys["common.allBlocks"] == "All blocks"


# ---------------------------------------------------------------------------
# Item 11: the ramp's colours
# ---------------------------------------------------------------------------


def test_the_diverging_ramp_uses_party_colours():
    """Item 11. The ramp ends must be the contest pair's own colours, the same
    ones the legend chips use, for whichever pair the AC contests."""
    tokens = (WEB / "lib" / "tokens.ts").read_text(encoding="utf-8")
    legend = (WEB / "components" / "DivergingLegend.tsx").read_text(encoding="utf-8")
    map_page = (WEB / "pages" / "MapExplorer.tsx").read_text(encoding="utf-8")

    assert "divergingPartyColor" in tokens
    assert "divergingPartySteps" in legend, (
        "the legend must build its swatches from the same function the markers "
        "use, or the two palettes drift"
    )
    assert "divergingPartyColor" in map_page
    assert "contest?.party_b" in map_page and "contest?.party_a" in map_page, (
        "the ramp must take the AC's own contest pair, not a fixed JMM/BJP"
    )


def test_the_ramp_arms_differ_in_lightness():
    """Section 6's requirement, and the objection item 11 has to answer.

    The previous blue/red ramp existed because JMM green and BJP saffron are
    hard to separate under red-green colour blindness. That holds when a ramp
    forces both arms to matched lightness. These two are not matched, and this
    test is what keeps that true if the palette changes: computed from the same
    fallback hex values `lib/tokens.ts` uses when CSS is unavailable.
    """
    text = (WEB / "lib" / "tokens.ts").read_text(encoding="utf-8")
    hexes = dict(re.findall(r"'(--party-[a-z]+)':\s*'(#[0-9a-fA-F]{6})'", text))
    assert "--party-jmm" in hexes and "--party-bjp" in hexes

    def luminance(value: str) -> float:
        r, g, b = (int(value[i : i + 2], 16) / 255 for i in (1, 3, 5))
        def lin(c: float) -> float:
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)

    gap = abs(luminance(hexes["--party-jmm"]) - luminance(hexes["--party-bjp"]))
    assert gap >= 0.15, (
        f"JMM and BJP differ by only {gap:.3f} in relative luminance. The map "
        "ramp puts them at opposite ends, and under red-green colour blindness "
        "lightness is the only channel left to tell them apart."
    )


# ---------------------------------------------------------------------------
# Item 12 and 13
# ---------------------------------------------------------------------------


def test_the_map_fits_its_markers():
    """Item 12. The view followed a hardcoded per-AC centre at a fixed zoom, so
    filtering to one block left its booths off screen."""
    fit = (WEB / "components" / "FitBounds.tsx").read_text(encoding="utf-8")
    map_page = (WEB / "pages" / "MapExplorer.tsx").read_text(encoding="utf-8")
    assert "<FitBounds" in map_page
    assert "fitBounds" in fit
    # The two cases that throw or look broken if unhandled.
    assert "points.length === 0" in fit, "empty bounds throws in Leaflet"
    assert "points.length === 1" in fit, (
        "a single point has zero extent and fitBounds zooms to maximum"
    )


@pytest.mark.parametrize("lang", ["en", "hi"])
def test_the_legend_explains_grey_markers(lang):
    """Item 13. Grey is the most common marker state on a partly loaded
    constituency, and the legend did not mention it."""
    value = flatten(load(lang))["map.greyMeans"]
    assert value, "map.greyMeans is empty"
    needle = "no data" if lang == "en" else "आँकड़ा नहीं"
    assert needle in value, (
        f"{lang}: the grey legend entry should say there is no data: {value!r}"
    )
