"""tirtha_bk URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from tirtha import admin_auth

PRE_URL = settings.PRE_URL

# The admin is mounted **before** the app, and the order is the whole point.
#
# `tirtha.urls` ends with the React shell's catch-all,
# `^project/tirtha/(?:[a-z]{2}/)?.*$`, and `include()` splices that module's
# patterns into this list where it appears. So every pattern in the app --
# catch-all included -- was matched before this line was ever reached, and the
# entire Django admin resolved to the SPA: `/admin/`, `/admin/login/` and every
# change list returned a 200 React shell.
#
# That took the operator's only interface with it. Banning a contributor, hiding
# a site or a run, setting `safety_status` and `admin_verdict`, releasing
# quarantined uploads, the `trigger_imageops`/`trigger_aVOps`/`trigger_GSOps`
# actions and approving site requests are all admin actions, and `admin.py` is
# ~2k lines precisely because it is the real moderation surface.
#
# The catch-all's own comment says "appended last so every route above wins",
# which is true of the file it lives in and not of the project. This line is the
# other half of that sentence. `tests/test_admin_reachable.py` asserts it,
# because the failure is invisible: the wrong answer is a 200.
#
# The slash-less form, redirected explicitly.
#
# `APPEND_SLASH` cannot do this. `CommonMiddleware` appends a slash only when
# the URL *without* one fails to resolve -- and `/project/tirtha/admin` resolves
# perfectly well, to the catch-all above. So typing the address without the
# trailing slash returned the SPA's "Page not found", with a 200, which is the
# same invisible failure the comment above describes: the operator concludes the
# admin has been removed.
#
# Before the include, for exactly the reason the admin itself is.
#
# The two `admin/login/` routes sit *above* `admin.site.urls` so they shadow
# the admin's own login view (`reverse("admin:login")` still names this path).
# `admin_auth.admin_login` always offers "Sign in with Google" and refuses the
# password form when `ADMIN_PASSWORD_LOGIN` is off; the `link/` route is the
# emergency `manage.py admin_login_link` URL. Both live under `admin/login/`
# so a prefix-matched nginx rate limit on that path covers them too.
urlpatterns = [
    path(PRE_URL + "admin/login/", admin_auth.admin_login, name="admin-login"),
    path(
        PRE_URL + "admin/login/link/<str:token>/",
        admin_auth.admin_login_link,
        name="admin-login-link",
    ),
    path(PRE_URL + "admin/", admin.site.urls),
    path(
        PRE_URL + "admin",
        RedirectView.as_view(url="/" + PRE_URL + "admin/", permanent=False),
    ),
    path("", include("tirtha.urls")),
] + static(PRE_URL + settings.STATIC_URL, document_root=settings.STATIC_ROOT)

# Only serve in testing. Otherwise use gunicorn or nginx
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Error handling
handler403 = "tirtha_bk.views.handler403"
handler404 = "tirtha_bk.views.handler404"
handler500 = "tirtha_bk.views.handler500"
