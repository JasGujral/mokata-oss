"""0.0.25 Lane D1 stage 5 — skill trigger tests: SHOULD-trigger and NEAR-MISS, deterministic.

A skill that triggers on the wrong project is as harmful as one that never triggers: the React web
pack's Server Component rules are wrong advice in React Native, and the NestJS guard rules mean
nothing in a bare Express app. Triggering here is the routing from stage 1 — graph surface +
the touched package's declared framework → `Spec.domains` → the skills develop/review load — so it
is tested as a pure function, with no model and no description matching.

Each row is (touched files, packages on disk) → which of the four new skills develop loads.
"""
import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import domains as dk

NEW = ("frontend-react", "backend-nestjs", "frontend-perf", "frontend-security")

PACKAGES = {
    "web": {"dependencies": {"react": "19", "react-dom": "19"}},
    "next": {"dependencies": {"next": "16", "react": "19"}},
    "ui-lib": {"peerDependencies": {"react": "19", "react-dom": "19"}},
    "native": {"dependencies": {"react": "19", "react-native": "0.81"}},
    "expo-web": {"dependencies": {"react": "19", "react-dom": "19", "expo": "54",
                                  "react-native-web": "0.21"}},
    "react-only": {"peerDependencies": {"react": "19"}},
    "nest": {"dependencies": {"@nestjs/core": "11", "@nestjs/common": "11"}},
    "express": {"dependencies": {"express": "5"}},
    "tslib": {"devDependencies": {"typescript": "5"}},
}

# (case, touched files, the subset of NEW that develop must load)
SHOULD = [
    ("react web component", ["web/src/components/Cart.tsx"], {"frontend-react"}),
    ("react web jsx", ["web/src/views/Home.jsx"], {"frontend-react"}),
    ("next app route", ["next/app/checkout/page.tsx"], {"frontend-react"}),
    ("component library (peer react-dom)", ["ui-lib/src/Button.tsx"], {"frontend-react"}),
    ("nest controller", ["nest/src/orders/orders.controller.ts"], {"backend-nestjs"}),
    ("nest module + dto", ["nest/src/orders/orders.module.ts",
                           "nest/src/orders/dto/create-order.dto.ts"], {"backend-nestjs"}),
    ("nest guard", ["nest/src/auth/roles.guard.ts"], {"backend-nestjs"}),
    ("react hot path", ["web/src/cache/useResultsCache.tsx"], {"frontend-react", "frontend-perf"}),
    ("react auth form", ["web/src/auth/LoginForm.tsx"], {"frontend-react", "frontend-security"}),
    ("nest cache: the nest pack, never the frontend-perf companion",
     ["nest/src/cache/cache.service.ts"], {"backend-nestjs"}),
    ("full-stack change", ["web/src/components/Cart.tsx", "nest/src/cart/cart.controller.ts"],
     {"frontend-react", "backend-nestjs"}),
]

NEAR_MISS = [
    ("react native screen", ["native/src/screens/Cart.tsx"]),
    ("expo app with a web target", ["expo-web/app/index.tsx"]),
    ("react without a DOM renderer", ["react-only/src/Thing.tsx"]),
    ("express controller-named file", ["express/src/users/users.controller.ts"]),
    ("express module-named file", ["express/src/app.module.ts"]),
    ("plain ts library", ["tslib/src/index.ts", "tslib/src/parse.service.ts"]),
    ("a python file named after react", ["tools/react_report.py"]),
    ("react native auth (security, not frontend-security)", ["native/src/auth/Login.tsx"]),
    ("tsx outside any package", ["loose/Widget.tsx"]),
]


class _Packages(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = cls._td.name
        for rel, data in PACKAGES.items():
            os.makedirs(os.path.join(cls.root, rel))
            with open(os.path.join(cls.root, rel, "package.json"), "w", encoding="utf-8") as fh:
                json.dump(data, fh)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def loads(self, files, stage="develop"):
        domains = dk.classify_domains(dk.surface_from_files(files, root=self.root))
        return set(dk.skills_for_stage(domains, stage)) & set(NEW)


class ShouldTrigger(_Packages):

    def test_each_should_trigger_case_loads_exactly_its_skills_in_develop_and_review(self):
        for case, files, want in SHOULD:
            for stage in ("develop", "review"):
                with self.subTest(case=case, stage=stage):
                    self.assertEqual(want, self.loads(files, stage))


class NearMiss(_Packages):

    def test_no_near_miss_loads_a_framework_skill(self):
        for case, files in NEAR_MISS:
            for stage in ("develop", "review"):
                with self.subTest(case=case, stage=stage):
                    self.assertEqual(set(), self.loads(files, stage))

    def test_the_generic_domain_still_fires_where_the_companion_does_not(self):
        native_auth = dk.classify_domains(
            dk.surface_from_files(["native/src/auth/Login.tsx"], root=self.root))
        self.assertIn("security", native_auth)
        nest_cache = dk.classify_domains(
            dk.surface_from_files(["nest/src/cache/cache.service.ts"], root=self.root))
        self.assertIn("performance", nest_cache)


class TriggeringIsDeterministicAndDescriptionFree(_Packages):

    def test_the_same_surface_always_routes_the_same_way(self):
        files = ["web/src/auth/LoginForm.tsx", "nest/src/orders/orders.controller.ts"]
        first = self.loads(files)
        for _ in range(5):
            self.assertEqual(first, self.loads(files))

    def test_routing_never_reads_a_skill_description(self):
        # Blank every new skill's frontmatter in a copy of the registry view: routing is unchanged,
        # because it reads the domain registry + package facts, never SKILL.md text.
        before = {case: self.loads(files) for case, files, _w in SHOULD}
        from unittest import mock
        with mock.patch("mokata.agent_skills.parse_frontmatter", return_value={}):
            after = {case: self.loads(files) for case, files, _w in SHOULD}
        self.assertEqual(before, after)

    def test_outside_develop_and_review_nothing_is_loaded(self):
        for stage in ("brainstorm", "spec", "test", "ship"):
            self.assertEqual([], dk.domain_skill_lines(["frontend-react", "backend-nestjs"], stage))


if __name__ == "__main__":
    unittest.main()
