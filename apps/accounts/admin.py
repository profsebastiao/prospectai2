from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse

from .access import scope_form, scoped_queryset, workspaces_for
from .models import Workspace, WorkspaceMembership


class WorkspaceMembershipInline(admin.TabularInline):
    model = WorkspaceMembership
    autocomplete_fields = ["usuario"]
    fields = ["usuario", "papel", "pode_gerenciar_gemini"]
    extra = 0


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ["nome", "slug"]
    search_fields = ["nome", "slug"]
    prepopulated_fields = {"slug": ["nome"]}
    inlines = [WorkspaceMembershipInline]

    def response_add(self, request, obj, post_url_continue=None):
        response = super().response_add(request, obj, post_url_continue)
        if (
            "_continue" not in request.POST and "_addanother" not in request.POST
            and "_popup" not in request.POST
            and workspaces_for(request.user).filter(pk=obj.pk).exists()
        ):
            request.session["workspace_id"] = obj.pk
            return redirect(reverse("integracao_ia") + "?primeiro=1")
        return response


class WorkspaceForeignKeys:
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        model = db_field.remote_field.model
        kwargs["queryset"] = scoped_queryset(request, model)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class WorkspaceModelAdmin(WorkspaceForeignKeys, admin.ModelAdmin):
    def get_queryset(self, request):
        return scoped_queryset(request, self.model, super().get_queryset(request))

    def has_add_permission(self, request):
        return request.workspace is not None and super().has_add_permission(request)

    def get_form(self, request, obj=None, **kwargs):
        form_class = super().get_form(request, obj, **kwargs)

        class ScopedForm(form_class):
            def __init__(self, *args, **form_kwargs):
                super().__init__(*args, **form_kwargs)
                scope_form(request, self)

        return ScopedForm