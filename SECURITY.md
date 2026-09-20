# Privacy and security

> **Quick take:** use [GitHub private vulnerability reporting](https://github.com/jessecmaddox3/photo-book-workshop/security/advisories/new) for security problems. Use invented reproductions and do not post personal books, catalogs, logs or keys in public issues.

This is a personal project without a guaranteed response time. Include the release version, affected command, expected boundary and minimal synthetic steps.

The review app listens only on loopback and validates Host, write Origin, paths, schema and workspace bindings. It has no internet authentication layer and should not be exposed through a reverse proxy or public tunnel. Browser draft storage and the local filesystem are personal working storage, not encrypted vaults.

Normal commands do not upload photos. Optional model commands require explicit consent; selected preview images and context are sent to the configured service. Provider files, account policies and billing are governed separately. Never include API keys in the book JSON, command-line arguments, screenshots, issue reports or source repository.

Archives, catalogs, candidate pools, contact sheets, books, notes, exports, request/result files and logs may contain personal information. Keep them outside the source repository and back them up privately. Normalization removes embedded image metadata but cannot remove identifying content visible in a picture.

Release packaging uses an explicit file allowlist and checks bundled asset provenance. Automated scans cannot recognize every personal detail someone deliberately adds to prose or an image. Review the exact files and visible content before publishing an adaptation.
