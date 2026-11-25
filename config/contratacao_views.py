from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.accounts.access import scoped_queryset
from apps.clients.models import Cliente
from apps.services.models import Servico
from apps.projects.models import Projeto, ProjetoServico


class ProjetoContratacaoForm(forms.ModelForm):
    servicos = forms.ModelMultipleChoiceField(
        queryset=Servico.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        label="Servicos contratados",
        required=True,
    )

    class Meta:
        model = Projeto
        fields = [
            "cliente", "nome", "descricao",
            "status", "data_inicio", "prazo_entrega"
        ]
        widgets = {
            "data_inicio": forms.DateInput(
                attrs={"type": "date"}, format="%Y-%m-%d"
            ),
            "prazo_entrega": forms.DateInput(
                attrs={"type": "date"}, format="%Y-%m-%d"
            ),
            "descricao": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cliente"].queryset = (
            scoped_queryset(request, Cliente).order_by("nome")
        )
        self.fields["servicos"].queryset = (
            scoped_queryset(request, Servico).filter(ativo=True).order_by("nome")
        )
        for nome in ("data_inicio", "prazo_entrega"):
            self.fields[nome].input_formats = ["%Y-%m-%d"]


@login_required
@require_http_methods(["GET", "POST"])
def projeto_contratacao_novo(request):
    inicial = {}
    cliente_id = request.GET.get("cliente", "")
    if cliente_id.isdigit() and scoped_queryset(request, Cliente).filter(
        pk=int(cliente_id)
    ).exists():
        inicial["cliente"] = int(cliente_id)

    form = ProjetoContratacaoForm(
        request,
        request.POST or None,
        initial=inicial,
    )

    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            projeto = form.save()
            for servico in form.cleaned_data["servicos"]:
                # get_or_create executa save() ao criar a associacao.
                # O save() existente pode gerar as tarefas padrao.
                scoped_queryset(request, ProjetoServico).get_or_create(
                    projeto=projeto,
                    servico=servico,
                )

        messages.success(request, "Projeto criado com sucesso.")
        return redirect("projeto_detalhe", pk=projeto.pk)

    return render(request, "contratacao_projeto.html", {
        "form": form,
    })
