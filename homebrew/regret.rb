# typed: strict
# frozen_string_literal: true

# Source of truth for the regret formula in the BytesAndCoffee/homebrew-tap tap.
# After each release, run scripts/update_homebrew_formula.py VERSION, test with
# `brew install --build-from-source`, and copy this file to the tap's Formula/.
class Regret < Formula
  include Language::Python::Virtualenv

  desc "Terminal client for Bad Decisions, an unofficial fan-made party card game"
  homepage "https://github.com/BytesAndCoffee/bad-decisions"
  url "https://files.pythonhosted.org/packages/a3/cb/dd7e4d8e8bede6f81e71d142cd7b398e197957dbeef72c21b5406b677f61/bad_decisions_client-2.0.2.tar.gz"
  sha256 "553b59d967453c7cb16ac8013401561f295730a3f22ac2929ee7fbebed893acb"
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
