# Homebrew formula for mokata.
#
# STATUS: NOT YET PUBLISHED. This formula is provided so a Homebrew tap can be stood up, but
# mokata is not in homebrew-core and no official tap is published yet. Until it is:
#   * install via pipx/pip/uvx (see docs/how-to/install-mokata.md) — those are live today, or
#   * self-tap this file (also in docs/how-to/install-mokata.md).
#
# GENERATED, NEVER HAND-EDITED. Three regions belong to
# `scripts/fill_homebrew_formula.py` — the top-level `url`, the top-level `sha256`, and
# everything between the GENERATED RESOURCES markers:
#
#     scripts/fill_homebrew_formula.py fill --version X.Y.Z --from-pypi
#     scripts/fill_homebrew_formula.py check --version X.Y.Z     # fail-closed gate
#
# Between releases the url/sha256 carry FILL-ME tokens so an unfilled formula is obviously
# unfilled (the previous hand-maintained formula instead drifted to a stale 0.0.5 URL and a
# fake checksum, which is the drift this flow exists to make impossible).
#
# The fill reads the PUBLISHED PyPI sdist, AFTER the release tag's PyPI job succeeds and
# before the tap push — not at release-prep. release.yml derives SOURCE_DATE_EPOCH from the
# commit being tagged and normalize_sdist.py stamps it into every tar member, so the published
# bytes are a function of a commit that does not exist yet at release-prep; a locally-built
# sdist cannot match them. The tap repo is JasGujral/homebrew-mokata; the maintainer's tap
# runbook sequences the fill, the local `brew audit`/`brew test` verification, and the push.
#
# Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
class Mokata < Formula
  include Language::Python::Virtualenv

  # `brew audit --strict` caps the description at 80 characters.
  desc "Spec-driven TDD framework for Claude Code — governed and human-gated"
  homepage "https://github.com/JasGujral/mokata-oss"
  url "FILL-ME-SDIST-URL"
  sha256 "FILL-ME-SDIST-SHA256"
  license "Apache-2.0"

  # mokata itself is pure Python, but Homebrew builds every resource from source
  # (`--no-binary=:all:`), and four of the vendored dependencies ship compiled cores:
  # pydantic-core and rpds-py are Rust, cryptography is Rust + OpenSSL, cffi is C.
  # Order is what `brew audit --strict`'s dependency-order cop requires.
  depends_on "rust" => :build
  depends_on "openssl@3"
  depends_on "python@3.12"

  # mokata's ONE unconditional runtime dependency is the MCP SDK (pyproject.toml), and
  # Homebrew installs Python formulae with `--no-deps` (Formula#std_pip_args), so the whole
  # transitive tree must be vendored here or `mokata-mcp` silently ships broken.
  # Regenerate with: scripts/fill_homebrew_formula.py refresh-lock
  # BEGIN GENERATED RESOURCES
  # END GENERATED RESOURCES

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "mokata", shell_output("#{bin}/mokata --version")

    # The CLI runs fine with the MCP SDK absent (it is lazily imported), so `--version` alone
    # would pass on a formula that forgot its resources and shipped a broken `mokata-mcp`.
    # Import the SDK through the formula's own venv so a missing resource fails LOUD, here,
    # instead of at a user's first `mokata-mcp` launch.
    system libexec/"bin/python", "-c", "import mcp"
    assert_match "mokata", shell_output("#{libexec}/bin/python -c " \
                                        "'import mokata.mcp_server as m; print(m.__name__)'")
  end
end
