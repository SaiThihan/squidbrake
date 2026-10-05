import os
import tempfile

# Hooks run by the tests (in this process or as subprocesses) note their calls here, never in the real
# ~/.squidbrake/hooks.log, where they would look like real agents using Squidbrake to `squidbrake doctor`.
os.environ["SQUIDBRAKE_HOOKLOG"] = os.path.join(tempfile.mkdtemp(prefix="squidbrake-tests-"), "hooks.log")
