"""Per-verb permission checks for the admin actions."""


def _may(request, codename: str) -> bool:
    """
    Superuser, or holds `tirtha.<codename>`.

    For the verbs that publish, destroy or spend GPU time, where "is staff and
    may edit this model" is too coarse (review 2026-09-04 2.7): a view-only or
    change-only staff account could otherwise ban contributors, unhide sites,
    release quarantine or start runs. The codenames are declared in each
    model's `Meta.permissions` (migration 0031), so each is grantable on its
    own. 0031 granted them to whoever already held the matching `change_*`
    permission -- `trigger_run` to holders of `change_run` and of
    `change_contribution`, since the trigger actions live on the Contribution
    admin -- so existing staff keep what they could do.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_active:
        return False
    return user.is_superuser or user.has_perm(f"tirtha.{codename}")
