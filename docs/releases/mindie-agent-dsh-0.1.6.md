# mindie-agent-dsh 0.1.6

Release preparation is in progress. The remote-dev 0.11.0 formal dependency
revision and final platform evidence must be recorded before this candidate is
published.

The shared remote-dev MCP runtime gains persistent SSH authentication. A user
provides a password once to verify access and install a managed public key when
possible. Password-only endpoints can reuse their saved credential from new
chats and independently installed plugin runtimes. Key repair occurs before a
business command; an uncertain business operation is not automatically replayed.

The default credential file is `~/.mindie/ssh/credentials.json`. Remote-dev owns
the host/port/user profiles and credential storage; the DSH adapter adds no
password cache or OS keychain dependency. No computer-account password prompt
is part of this flow.

The runtime retains knowledge 0.13.3 and diagnostics 0.5.4. Existing host loading,
session identity, knowledge activation, and component installation boundaries
remain unchanged. Historical host compatibility entries retain their original
versions and evidence.

Shared runtime acceptance exercises real OpenSSH and the DSH harness profile
from installed packages. It does not by itself establish a native DSH host
reload or automatic activation in an existing user installation. Final release
evidence must identify the actual platform, interpreter, component revisions,
and consumer exercised; final tag test and benchmark reports and checksums
must be attached to the formal release.
