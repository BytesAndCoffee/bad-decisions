# Source of truth for the regret formula in the BytesAndCoffee/homebrew-tap tap.
# After each release, run scripts/update_homebrew_formula.py VERSION, test with
# `brew install --build-from-source`, and copy this file to the tap's Formula/.
class Regret < Formula
  include Language::Python::Virtualenv

  desc "Terminal client for Bad Decisions, an unofficial fan-made party card game"
  homepage "https://github.com/BytesAndCoffee/bad-decisions"
  url "https://files.pythonhosted.org/packages/75/c0/78f0cecc2e14b5424692d9937754187920edbd3d173177b538503414d25c/bad_decisions_client-2.0.1.tar.gz"
  sha256 "046d65ec2b78cbc646b9cd4a133ec4b30636f87b43057c9908e1086786214ba0"
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
