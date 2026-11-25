from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .access import workspaces_for


@login_required
@require_http_methods(["GET", "POST"])
def workspace_select(request):
    workspaces = workspaces_for(request.user)
    if request.method == "POST":
        selected = request.POST.get("workspace", "")
        if not selected.isascii() or not selected.isdecimal() or len(selected) > 19:
            raise Http404
        workspace = get_object_or_404(workspaces, pk=selected)
        request.session["workspace_id"] = workspace.pk
        return redirect("dashboard")
    return render(request, "workspace_select.html", {"workspaces": workspaces})