# typed: strict
# frozen_string_literal: true

# Source of truth for the regret formula in the BytesAndCoffee/homebrew-tap tap.
# After each release, run scripts/update_homebrew_formula.py VERSION, test with
# `brew install --build-from-source`, and copy this file to the tap's Formula/.
class Regret < Formula
  include Language::Python::Virtualenv

  desc "Terminal client for Bad Decisions, an unofficial fan-made party card game"
  homepage "https://github.com/BytesAndCoffee/bad-decisions"
  url "https://files.pythonhosted.org/packages/26/88/82464035fb8f0e8b3f179b671d4a0112f9b305f0b275067ec2a55ff34ba9/bad_decisions_client-2.1.0.tar.gz"
  sha256 "ec15fc0193be799bf4b1937ce9f6821e59cc75be1aa4085b5cc7f08eb5f72bb0"
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
