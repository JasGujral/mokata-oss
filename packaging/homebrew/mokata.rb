# Homebrew formula for mokata.
#
# STATUS: NOT YET PUBLISHED. This formula is provided so a Homebrew tap can be stood up, but
# mokata is not in homebrew-core and no official tap is published yet. Until it is:
#   * install via pipx/pip/uvx (see docs/how-to/install-mokata.md) — those are live today, or
#   * self-tap this file (also in docs/how-to/install-mokata.md).
#
# To publish: fill `url` with the released PyPI sdist and `sha256` with its real checksum
# (`brew fetch` / `shasum -a 256`), bump the version, and push to a tap repo (e.g.
# `JasGujral/homebrew-mokata`). Do NOT claim this is published until that is done.
#
# Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
class Mokata < Formula
  include Language::Python::Virtualenv

  desc "Spec-driven TDD framework for Claude Code — governed, knowledge-aware, human-gated"
  homepage "https://github.com/JasGujral/mokata-oss"
  # PENDING PUBLICATION — replace with the real released sdist URL + checksum at publish time.
  url "https://files.pythonhosted.org/packages/source/m/mokata/mokata-0.0.5.tar.gz"
  sha256 "pending-publication-fill-with-real-sdist-checksum"
  license "Apache-2.0"

  # mokata's core has NO required runtime dependencies (every external tool is an optional,
  # degrade-clean extra), so the formula only needs Python.
  depends_on "python@3.12"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "mokata", shell_output("#{bin}/mokata --version")
  end
end
