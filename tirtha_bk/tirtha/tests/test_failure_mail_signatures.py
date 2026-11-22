"""
Every failure notifier can be called the way its caller calls it.

`tasks.post_save_contrib_imageops` passed `processing_step=` to
`send_image_processing_failure_email`, which had no such parameter. So every
ImageOps failure raised `TypeError` *inside the notifier*, and the caller's own
`except Exception` filed the signature mismatch as a contribution-lookup
failure. That is the ingest queue's only alerting path and it had never once
alerted: the production journal carries it twice on 2026-09-05, both behind
`SafetyUnavailable('nudenet is not installed')` -- the outage that stopped every
upload from being checked.

Nothing caught it because nothing called these functions with a real caller's
arguments. That is what this does: bind each production call site's keywords
against the signature it targets. It needs no database and no mail backend --
a mismatch is a `TypeError` from `Signature.bind`, which is the same error the
call site would raise.
"""

from __future__ import annotations

import inspect
import re

from django.test import SimpleTestCase

from tirtha import email_utils, tasks, views
from tirtha.workers import base as workers_base
from tirtha.workers import runner as workers_runner

# (module, the function it calls, the kwargs it passes)
CALL_SITES = [
    (tasks, "send_image_processing_failure_email"),
    (views.uploads, "send_image_processing_failure_email"),
    # `workers` is a package now; these are the modules its call sites moved to.
    (workers_base, "send_reconstruction_failure_email"),
    (workers_runner, "send_reconstruction_failure_email"),
    (workers_base, "send_contributor_run_failed_email"),
]


def _kwargs_at_call_site(module, function_name):
    """The keyword names a module passes to `function_name`, by reading it."""
    source = inspect.getsource(module)
    found = []
    for match in re.finditer(rf"{function_name}\((.*?)\n\s*\)", source, re.S):
        found.extend(re.findall(r"(\w+)\s*=", match.group(1)))
    return found


class EveryCallSiteMatchesItsSignature(SimpleTestCase):
    def test_each_notifier_accepts_what_its_caller_passes(self):
        problems = []
        for module, name in CALL_SITES:
            function = getattr(email_utils, name)
            signature = inspect.signature(function)
            passed = _kwargs_at_call_site(module, name)
            if not passed:
                problems.append(f"{module.__name__}: no call to {name} found")
                continue
            unknown = [k for k in passed if k not in signature.parameters]
            if unknown:
                problems.append(
                    f"{module.__name__} passes {unknown} to {name}, which takes "
                    f"{list(signature.parameters)}"
                )
        self.assertEqual(problems, [], "\n  ".join(problems))

    def test_the_call_that_broke_binds(self):
        """The exact keywords `tasks.post_save_contrib_imageops` passes."""
        signature = inspect.signature(email_utils.send_image_processing_failure_email)
        signature.bind(
            contribution_id="c",
            site_id="s",
            site_name="n",
            contributor_email="e@example.org",
            processing_step="Image Processing (ImageOps)",
            error_message="boom",
            log_file_path=None,
        )

    def test_the_other_caller_still_binds_without_it(self):
        """`views/uploads.py` omits the keyword; the default has to cover it."""
        signature = inspect.signature(email_utils.send_image_processing_failure_email)
        bound = signature.bind(
            contribution_id="c",
            site_id="s",
            site_name="n",
            contributor_email="e@example.org",
            error_message="boom",
        )
        bound.apply_defaults()
        self.assertEqual(bound.arguments["processing_step"], "Image Processing")

    def test_the_notifiers_own_errors_are_not_filed_as_lookup_failures(self):
        """
        The caller caught bare `Exception`, so a programming error in the
        notifier was logged as "Failed to get contribution details for email".
        """
        source = inspect.getsource(tasks.post_save_contrib_imageops)
        self.assertNotIn("except Exception as contrib_error", source)
        self.assertIn("Contribution.DoesNotExist", source)
