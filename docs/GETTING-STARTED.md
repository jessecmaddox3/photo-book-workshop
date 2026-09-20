# Your first photo book

> **Quick take:** the starter makes a private, editable copy of an invented five-page book, opens a local review page, and leaves the original download alone.

## Download and open

On the [release page](https://github.com/jessecmaddox3/photo-book-workshop/releases/latest), expand **Assets** if necessary and download the named project ZIP. Extract it before opening anything. You can put the extracted folder in Documents or another place you recognize.

Install Python 3.12 or newer from [python.org](https://www.python.org/downloads/). This is the program that runs the workshop. A GitHub account is unnecessary. Python does not need access to a photo account.

On **Mac**, open `Start.command`. If it opens as text or is not executable, open Terminal, type `bash ` with a space, drag `Start.command` into that window, and press Return. If macOS blocks a downloaded file, use Apple’s normal approval controls only after checking the download came from this project. Do not disable your computer’s protections.

On **Windows**, open `Start.cmd` after extracting the ZIP. If Windows cannot find Python, rerun its installer with the PATH option, then open a fresh terminal or the starter again. If the extension is hidden, look for the file whose type is “Windows Command Script.”

On **Linux**, install Python 3.12+ and your distribution’s Python venv package, open a terminal in the extracted folder, and run `sh Start.sh`.

The first run creates `.venv` inside the extracted project and downloads Python dependencies. It prints its progress. Later runs reuse that environment. These installation downloads are separate from the local example, which makes no photo-service requests.

## What opens

The address begins with `http://127.0.0.1:`. That means your own computer. The workshop saves and reuses this workspace’s port so browser drafts remain recoverable after restarting. The app is not an internet website, and another person cannot open it from that address on their device.

The example lives in **Photo Book Workshop/Example** inside your home folder. The starter prints the exact location. It contains six invented illustrations, a local catalog, contact sheets, a page plan, a first proof and, after review, your choices. These are all copies made for you.

Use the page navigation to move through the book. The caption shown as a preview is not automatically selected. Click an option to choose it. **Clear choice** restores the unchosen state. A note is an instruction for later editing; it does not become the printed caption.

Wait for the saved indicator before closing the page. The summary lets you inspect and download your choices. **Make proof** builds a fresh PDF, browser pages and printable review sheets using confirmed choices. Open those links to inspect the result. Each build gets its own folder.

If two tabs make competing changes, the app shows both versions and asks you to keep yours or use the saved version. If browser storage is unavailable, a warning explains what is temporary. Export pending words before closing a tab that cannot save. Recovery controls let you inspect and recover another interrupted tab’s pending work.

## Return later

Keep the terminal window open during review. To stop, press Ctrl+C there. Open the starter again to reopen the same example. Do not delete its `review` folder to fix an error; that folder holds saved choices and build outputs.

To start a second independent example, open a terminal in the downloaded project and run:

```sh
python3 launch.py --directory "../another-example"
```

On Windows use `py -3 launch.py --directory "..\another-example"`. You can choose another folder name. Existing example folders reopen; `demo --directory` deliberately requires a new folder.

## Common bumps

| What happens | What to do |
| --- | --- |
| The first setup cannot download packages | Check internet access and retry the starter. Keep the printed error if it still fails. |
| The browser did not open | Copy the full `127.0.0.1` address printed in the terminal into your browser. |
| The page stopped responding | Check that its terminal is still open. Restart the starter and use the newly printed address. |
| The saved address is unavailable | Stop another instance of the same workspace, or free its port, then retry. The app will not silently switch addresses and strand browser drafts. |
| Python is too old | Install 3.12 or newer. The starter prints the version requirement instead of modifying your system Python. |
| “Book changed” or a damaged-state error | Preserve the folder. Export any pending browser work. Use a separate review workspace for an intentionally changed book. See [recovery](REFERENCE.md#review-and-recovery). |
| A proof fails because text or images do not fit | Shorten or resize the actual text, change the page layout or choose another crop. The last successful proof remains available on disk. |

The next step is [using your own photos](OWN-PHOTOS.md). You can try every ordinary feature without enabling AI.
