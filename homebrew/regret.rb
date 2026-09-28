# typed: strict
# frozen_string_literal: true

# Source of truth for the regret formula in the BytesAndCoffee/homebrew-tap tap.
# After each release, run scripts/update_homebrew_formula.py VERSION, test with
# `brew install --build-from-source`, and copy this file to the tap's Formula/.
class Regret < Formula
  include Language::Python::Virtualenv

  desc "Terminal client for Bad Decisions, an unofficial fan-made party card game"
  homepage "https://github.com/BytesAndCoffee/bad-decisions"
  url "https://files.pythonhosted.org/packages/12/c6/cd5a4728a6576ca1728222579002ad32b26b7f7d6a645e5b867a57419ff4/bad_decisions_client-2.0.3.tar.gz"
  sha256 "6881101ff93641103669771637b577cc2fcb930c10f0c706204d66b57f8babc2"
  license "MIT"

  depends_on "python@3.13"

  def install
    # Also links the wheel's share/man/man1/regret.1 into Homebrew's man path.
    virtualenv_install_with_resources
  end

  test do
    assert_match "regret #{version}", shell_output("#{bin}/regret --version")
    assert_path_exists man1/"regret.1"
    assert_match "installed with homebrew", shell_output("#{bin}/regret doctor --api-url http://127.0.0.1:9 2>&1", 1)
  end
end
