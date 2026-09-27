# Source of truth for the regret formula in the BytesAndCoffee/homebrew-tap tap.
# After each release, run scripts/update_homebrew_formula.py VERSION, test with
# `brew install --build-from-source`, and copy this file to the tap's Formula/.
class Regret < Formula
  include Language::Python::Virtualenv

  desc "Terminal client for Bad Decisions, an unofficial fan-made party card game"
  homepage "https://github.com/BytesAndCoffee/bad-decisions"
  url "https://files.pythonhosted.org/packages/a1/69/ce43017f271cd6051ba2640d2782555fa526c5363cf487960db841268b47/bad_decisions_client-1.8.5.tar.gz"
  sha256 "5789eae1d778dd5b58c6037a1d477568e3130e967d229c83548f064b9c94be47"
  license "MIT"

  depends_on "python@3.13"

  resource "packaging" do
    url "https://files.pythonhosted.org/packages/7d/fa/3944b40b07da9ce895c0e6303a5ab7d53da063554f534556b134a54d6093/packaging-26.3.tar.gz"
    sha256 "94edc256424af38762eb31306eed28beb9f0efc50a8837492c9d6fd6004aed79"
  end

  def install
    # Also links the wheel's share/man/man1/regret.1 into Homebrew's man path.
    virtualenv_install_with_resources
  end

  test do
    assert_match "regret #{version}", shell_output("#{bin}/regret --version")
    assert_path_exists man1/"regret.1"
  end
end
