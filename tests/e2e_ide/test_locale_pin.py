"""IDE e2e -- the harness pins the page's locale (0.0.28 H, the behavioural guard for it).

Both browsers default to en-US on a developer's Mac and on ubuntu runners, so a harness that
dropped its `locale=` would still read en-US and every check against "en-US" would stay green. This
flow therefore pins a locale NO host defaults to (de-DE): if `new_page` is not given the harness's
`LOCALE`, the page reports the host's language instead and these steps (and the setUpClass self-check)
red.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import _harness as H


class TheHarnessPinsThePagesLocale(H.IdeFlow):
    FIXTURE = "shop"
    LOCALE = "de-DE"
    SELF_CHECK_LOCALE = False        # the steps below ARE the check, so a drop reds as a step

    def test_1_the_page_reports_the_pinned_language(self):
        self.assertEqual("de-DE", self.page.evaluate("navigator.language"))

    def test_2_the_page_formats_numbers_in_the_pinned_locale(self):
        self.assertEqual("1.234,5", self.page.evaluate("(1234.5).toLocaleString(navigator.language)"))
