EAPI=8

DESCRIPTION="Meta and utility scripts for AthanorOS"
HOMEPAGE="https://choccynix.github.io/"
LICENSE="MIT"
SLOT="0"
KEYWORDS="~amd64"

RDEPEND="
    app-shells/bash
    sys-apps/portage
"

src_install() {
    dodoc README.md 2>/dev/null || true
}
