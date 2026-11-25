import mimetypes

from django import forms
from django.contrib.auth import (
    get_user_model, update_session_auth_hash
)
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.files.storage import default_storage
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.contrib import messages

from apps.accounts.access import pode_editar, scoped_queryset
from apps.accounts.models import PerfilUsuario
from apps.clients.models import Cliente
from apps.projects.models import Projeto, ProjetoServico
from apps.tasks.models import Tarefa


class PerfilForm(forms.ModelForm):
    class Meta:
        model = get_user_model()
        fields = ["first_name", "last_name", "email"]


class FotoPerfilForm(forms.Form):
    foto = forms.ImageField(
        label="Foto de perfil", required=False,
        help_text="JPG, PNG ou WebP. Tamanho máximo: 5 MB.",
        widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
    )
    remover_foto = forms.BooleanField(label="Remover foto atual", required=False)

    def clean_foto(self):
        foto = self.cleaned_data.get("foto")
        if foto and foto.size > 5 * 1024 * 1024:
            raise forms.ValidationError("A imagem deve ter no máximo 5 MB.")
        return foto

    def clean(self):
        dados = super().clean()
        if dados.get("foto") and dados.get("remover_foto"):
            raise forms.ValidationError("Escolha uma foto nova ou marque a remoção, não as duas ações.")
        return dados


@login_required
def foto_perfil_usuario(request):
    perfil = PerfilUsuario.objects.filter(usuario=request.user).only("foto").first()
    if not perfil or not perfil.foto:
        raise Http404
    content_type = mimetypes.guess_type(perfil.foto.name)[0] or "application/octet-stream"
    resposta = FileResponse(perfil.foto.open("rb"), content_type=content_type)
    resposta["Cache-Control"] = "private, max-age=300"
    resposta["X-Content-Type-Options"] = "nosniff"
    return resposta


@login_required
def cliente_detalhe(request, pk):
    cliente = get_object_or_404(scoped_queryset(request, Cliente), pk=pk)
    projetos = scoped_queryset(request, Projeto).filter(
        cliente=cliente
    ).order_by("-pk")

    hoje = timezone.localdate()
    tarefas_urgentes = (
        scoped_queryset(request, Tarefa)
        .filter(projeto__cliente=cliente, prazo__lte=hoje)
        .exclude(status=Tarefa.Status.CONCLUIDA)
        .select_related("projeto")
        .order_by("prazo", "pk")
    )

    return render(request, "cliente_detalhe.html", {
        "cliente": cliente,
        "projetos_abertos": projetos.exclude(status=Projeto.Status.CONCLUIDO),
        "projetos_concluidos": projetos.filter(status=Projeto.Status.CONCLUIDO),
        "tarefas_urgentes": tarefas_urgentes,
        "hoje": hoje,
    })


@login_required
def projeto_detalhe(request, pk):
    projeto = get_object_or_404(
        scoped_queryset(request, Projeto).select_related("cliente"),
        pk=pk
    )

    servicos = scoped_queryset(request, ProjetoServico).filter(
        projeto=projeto
    ).select_related("servico")

    tarefas = list(
        scoped_queryset(request, Tarefa).filter(projeto=projeto)
        .order_by("prazo", "pk")
    )

    abertas = []
    concluidas = []
    for tarefa in tarefas:
        grupo = concluidas if tarefa.status == Tarefa.Status.CONCLUIDA else abertas
        grupo.append(tarefa)
    total = len(tarefas)

    return render(request, "projeto_detalhe.html", {
        "projeto": projeto,
        "servicos": servicos,
        "tarefas_abertas": abertas,
        "tarefas_concluidas": concluidas,
        "total": total,
        "concluidas": len(concluidas),
        "progresso": round(
            len(concluidas) * 100 / total
        ) if total else 0,
        "pode_ver_cliente": True,
        "pode_editar_projeto": pode_editar(request),
        "pode_editar_tarefa": True,
    })


@login_required
def configuracoes(request):
    usuario = request.user
    perfil = PerfilForm(instance=usuario)
    senha = PasswordChangeForm(usuario)
    perfil_visual = PerfilUsuario.objects.filter(usuario=usuario).first()
    foto_perfil = FotoPerfilForm()

    if request.method == "POST":
        acao = request.POST.get("acao")

        if acao == "perfil":
            perfil = PerfilForm(request.POST, instance=usuario)
            if perfil.is_valid():
                perfil.save()
                return redirect("configuracoes")

        elif acao == "foto_perfil":
            foto_perfil = FotoPerfilForm(request.POST, request.FILES)
            if foto_perfil.is_valid():
                nova_foto = foto_perfil.cleaned_data["foto"]
                remover = foto_perfil.cleaned_data["remover_foto"]
                if nova_foto or remover:
                    if perfil_visual is None:
                        perfil_visual = PerfilUsuario(usuario=usuario)
                    arquivo_antigo = perfil_visual.foto.name if perfil_visual.pk and perfil_visual.foto else ""
                    if remover:
                        perfil_visual.foto = ""
                    else:
                        perfil_visual.foto = nova_foto
                    perfil_visual.save()
                    if arquivo_antigo and (remover or nova_foto):
                        transaction.on_commit(lambda nome=arquivo_antigo: default_storage.delete(nome))
                    messages.success(request, "Foto de perfil atualizada.")
                return redirect("configuracoes")

        elif acao == "senha":
            senha = PasswordChangeForm(usuario, request.POST)
            if senha.is_valid():
                usuario = senha.save()
                update_session_auth_hash(request, usuario)
                return redirect("configuracoes")

    return render(request, "configuracoes.html", {
        "perfil": perfil,
        "senha": senha,
        "usuario": usuario,
        "perfil_visual": perfil_visual,
        "foto_perfil": foto_perfil,
    })
