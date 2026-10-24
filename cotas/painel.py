"""Painel do usuário: folhas restantes hoje, aviso de bloqueio e área do administrador."""
import ctypes
import getpass
import json
import os
import socket
import subprocess
import sys
import tkinter as tk
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from . import config

CORES = {
    "fundo": "#f6f7f9", "cartao": "#ffffff", "texto": "#1f2328", "suave": "#656d76",
    "verde": "#1a7f37", "laranja": "#bc4c00", "vermelho": "#cf222e", "borda": "#d0d7de",
    "aviso_fundo": "#fff8c5", "aviso_borda": "#d4a72c", "aviso_texto": "#6f4b00",
    "erro_fundo": "#ffebe9", "erro_borda": "#ff8182", "erro_texto": "#82071e",
}
SITUACOES = {
    "bloqueado": ("✗", "vermelho", "bloqueado"),
    "estornado": ("↺", "suave", "devolvido"),
    "desativado": ("✓", "suave", "não descontado"),
}
TRAVAR_ADMIN_MS = 5 * 60 * 1000


class ErroAPI(Exception):
    pass


class Servico:
    def __init__(self, porta, usuario):
        self.base = f"http://127.0.0.1:{porta}"
        self.usuario = usuario

    def saldo(self):
        url = f"{self.base}/saldo?" + urllib.parse.urlencode({"usuario": self.usuario})
        with urllib.request.urlopen(url, timeout=3) as r:
            return json.load(r)

    def enviar(self, caminho, dados):
        req = urllib.request.Request(self.base + caminho, json.dumps(dados).encode(),
                                     {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            try:
                msg = json.load(e)["erro"]
            except Exception:
                msg = f"Erro {e.code}."
            raise ErroAPI(msg)
        except OSError:
            raise ErroAPI("O controle de impressão não está respondendo.")


def instancia_unica(usuario):
    """A primeira janela escuta numa porta própria do usuário. Se o ícone for
    aberto de novo, a segunda cópia só pede para a primeira aparecer e fecha."""
    porta = 40000 + zlib.crc32(usuario.encode()) % 20000
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", porta))
        s.listen(5)
        s.setblocking(False)
        return s
    except OSError:
        s.close()
        try:
            socket.create_connection(("127.0.0.1", porta), timeout=2).close()
        except OSError:
            pass
        return None


class Painel:
    def __init__(self, raiz, usuario, servico, trava):
        self.raiz, self.usuario, self.servico, self.trava = raiz, usuario, servico, trava
        self.ultimo_id = None
        self.controle_ativo = True
        self.senha_admin = None
        self.timer_trava = None
        self.limites = {}
        self.alerta = None
        self.lista_atual = None

        raiz.title("Minha cota de impressão")
        raiz.configure(bg=CORES["fundo"])
        raiz.protocol("WM_DELETE_WINDOW", self.minimizar)
        base = tkfont.nametofont("TkDefaultFont").actual("family")
        self.fontes = {"peq": (base, 9), "normal": (base, 10), "neg": (base, 10, "bold"),
                       "titulo": (base, 11), "grande": (base, 13, "bold"),
                       "numero": (base, 48, "bold")}
        self.configurar_estilo()
        self.montar_principal()
        self.montar_admin()
        self.atualizar()
        self.escutar_outras_aberturas()

    # ------------------------------------------------------------ peças da tela
    def texto(self, pai, texto="", fonte="normal", cor="texto", fundo="cartao", **pack):
        rotulo = tk.Label(pai, text=texto, font=self.fontes[fonte], fg=CORES[cor],
                          bg=CORES[fundo], justify="left", wraplength=320)
        rotulo.pack(anchor="w", **pack)
        return rotulo

    def linha(self, pai, fundo="cartao"):
        quadro = tk.Frame(pai, bg=CORES[fundo])
        quadro.pack(fill="x")
        return quadro

    def caixa(self, fundo="cartao", borda="borda", **kw):
        return tk.Frame(self.raiz, bg=CORES[fundo], highlightthickness=1,
                        highlightbackground=CORES[borda], highlightcolor=CORES[borda], **kw)

    def secao(self, pai, titulo):
        tk.Frame(pai, height=1, bg=CORES["borda"]).pack(fill="x", pady=(8, 6))
        self.texto(pai, titulo, "neg", pady=(0, 3))

    def janela(self, titulo, cor="texto"):
        j = tk.Toplevel(self.raiz, bg=CORES["cartao"], padx=22, pady=18)
        j.title(titulo)
        j.transient(self.raiz)
        self.texto(j, titulo, "grande", cor)
        return j

    def centralizar(self, j):
        j.update_idletasks()
        x = self.raiz.winfo_rootx() + (self.raiz.winfo_width() - j.winfo_reqwidth()) // 2
        j.geometry(f"+{max(0, x)}+{max(0, self.raiz.winfo_rooty() + 30)}")
        j.lift()
        j.focus_force()

    def ajustar_tamanho(self):
        # Alguns gerenciadores de janela (como o do Zorin) mantêm o tamanho antigo
        # quando algo aparece ou some; isto faz a janela acompanhar o conteúdo
        self.raiz.update_idletasks()
        self.raiz.geometry("")

    def configurar_estilo(self):
        estilo = ttk.Style(self.raiz)
        if "clam" in estilo.theme_names():
            estilo.theme_use("clam")
        estilo.configure("TButton", padding=(10, 4))
        for cor in ("verde", "laranja", "vermelho"):
            estilo.configure(f"{cor}.Horizontal.TProgressbar", background=CORES[cor],
                             lightcolor=CORES[cor], darkcolor=CORES[cor], thickness=10,
                             troughcolor="#eaeef2", bordercolor=CORES["borda"])

    # ------------------------------------------------------------ tela principal
    def montar_principal(self):
        self.aviso = self.caixa("aviso_fundo", "aviso_borda", padx=12, pady=8)
        self.lbl_aviso = self.texto(self.aviso, fonte="peq", cor="aviso_texto", fundo="aviso_fundo")
        self.problema = self.caixa("erro_fundo", "erro_borda", padx=12, pady=8)
        self.lbl_problema = self.texto(self.problema, fonte="peq", cor="erro_texto", fundo="erro_fundo")

        self.cartao = self.caixa(padx=20, pady=16)
        self.cartao.pack(padx=14, pady=(14, 10), fill="x")
        self.texto(self.cartao, f"Olá, {self.usuario}", "titulo", "suave")
        self.lbl_numero = self.texto(self.cartao, "–", "numero", pady=(4, 0))
        self.lbl_sub = self.texto(self.cartao, "Conectando…")
        self.barra = ttk.Progressbar(self.cartao, length=280, style="verde.Horizontal.TProgressbar")
        self.barra.pack(fill="x", pady=(10, 4))
        self.lbl_renova = self.texto(self.cartao, fonte="peq", cor="suave")

        self.historico = tk.Frame(self.raiz, bg=CORES["fundo"])
        self.historico.pack(fill="x", padx=16, pady=(0, 4))
        self.texto(self.historico, "Últimas impressões", cor="suave", fundo="fundo")
        self.lista = tk.Frame(self.historico, bg=CORES["fundo"])
        self.lista.pack(fill="x", pady=(2, 6))

    def mostrar(self, d):
        if d["isento"]:
            self.lbl_numero.config(text="∞", fg=CORES["verde"])
            self.lbl_sub.config(text="Você pode imprimir sem limite.")
            self.barra.config(value=100, style="verde.Horizontal.TProgressbar")
            self.lbl_renova.config(text="")
        else:
            resto, total = d["restantes"], d["total"]
            pct = resto / total * 100 if total else 0
            cor = "verde" if pct > 50 else "laranja" if pct > 20 and resto else "vermelho"
            extra = f" (+{d['extra']} extra)" if d["extra"] else ""
            self.lbl_numero.config(text=str(resto), fg=CORES[cor])
            self.lbl_sub.config(text=f"{'folha restante' if resto == 1 else 'folhas restantes'} "
                                     f"hoje · de {total}{extra}")
            self.barra.config(value=pct, style=f"{cor}.Horizontal.TProgressbar")
            self.lbl_renova.config(text=f"Usadas hoje: {d['usados']} · Renova em {d['renova_em']}")
        self.mostrar_lista(d["trabalhos"])

    def mostrar_lista(self, trabalhos):
        # Só redesenha se algo mudou; redesenhar a cada 3 s faz a lista piscar
        if trabalhos == self.lista_atual:
            return
        self.lista_atual = trabalhos
        for w in self.lista.winfo_children():
            w.destroy()
        if not trabalhos:
            self.texto(self.lista, "Nenhuma impressão ainda.", "peq", "suave", "fundo")
        for t in trabalhos[:5]:
            titulo = t["titulo"] or "Documento"
            titulo = titulo if len(titulo) <= 28 else titulo[:27] + "…"
            dia = "" if t["data"] == trabalhos[0]["data"] else f"{t['data'][8:10]}/{t['data'][5:7]} "
            folhas = f"{t['folhas']} folha" + ("" if t["folhas"] == 1 else "s")
            simbolo, cor, fim = SITUACOES.get(t["situacao"], ("✓", "verde", folhas))
            linha = self.linha(self.lista, "fundo")
            self.texto(linha, simbolo, cor=cor, fundo="fundo", side="left")
            self.texto(linha, f"{dia}{t['hora'][:5]}  {titulo}", "peq", fundo="fundo",
                       side="left", padx=(4, 0))
            self.texto(linha, fim, "peq", cor, "fundo", side="right")

    def mostrar_offline(self):
        self.lbl_numero.config(text="?", fg=CORES["suave"])
        self.lbl_sub.config(text="O controle de impressão não está respondendo.\n"
                                 "Se não conseguir imprimir, procure o suporte.")
        self.barra.config(value=0)
        self.lbl_renova.config(text="")

    def mostrar_problemas(self, problemas):
        if not problemas:
            if self.problema.winfo_ismapped():
                self.problema.pack_forget()
                self.ajustar_tamanho()
            return
        self.lbl_problema.config(text="\n\n".join(p["mensagem"] for p in problemas))
        if not self.problema.winfo_ismapped():
            self.problema.pack(padx=14, pady=(14, 0), fill="x", before=self.cartao)
            self.ajustar_tamanho()

    def mostrar_estado(self, d):
        self.controle_ativo = d["controle_ativo"]
        if self.controle_ativo:
            if self.aviso.winfo_ismapped():
                self.aviso.pack_forget()
                self.ajustar_tamanho()
            if self.senha_admin and self.admin_aberto():
                self.btn_controle.config(text="Desativar controle")
                self.lbl_estado.config(text="ATIVO: cada usuário tem seu limite diário.")
            return
        desde = f" desde {d['controle_alterado_em']}" if d["controle_alterado_em"] else ""
        self.lbl_aviso.config(text=f"Controle de impressão desativado{desde}.\n"
                                   "As impressões estão liberadas e não são descontadas.")
        if not self.aviso.winfo_ismapped():
            self.aviso.pack(padx=14, pady=(14, 0), fill="x", before=self.cartao)
            self.ajustar_tamanho()
        if self.senha_admin and self.admin_aberto():
            self.btn_controle.config(text="Reativar controle")
            self.lbl_estado.config(text="DESATIVADO: todos imprimem sem limite e nada é descontado.")

    # ------------------------------------------------------------ atualização
    def atualizar(self):
        try:
            dados = self.servico.saldo()
        except (OSError, ValueError):
            self.mostrar_offline()
        else:
            self.mostrar(dados)
            self.mostrar_estado(dados)
            self.mostrar_problemas(dados["impressoras_com_problema"])
            self.verificar_bloqueios(dados["trabalhos"])
        self.raiz.after(3000, self.atualizar)

    def atualizar_agora(self):
        try:
            self.mostrar(self.servico.saldo())
        except (OSError, ValueError):
            pass

    def escutar_outras_aberturas(self):
        try:
            while True:
                self.trava.accept()[0].close()
                self.mostrar_janela()
        except OSError:
            pass
        self.raiz.after(400, self.escutar_outras_aberturas)

    def mostrar_janela(self):
        self.raiz.deiconify()
        self.raiz.lift()
        self.raiz.attributes("-topmost", True)
        self.raiz.after(800, lambda: self.raiz.attributes("-topmost", False))
        self.raiz.focus_force()

    def minimizar(self):
        # O painel nunca fecha de verdade, para continuar avisando dos bloqueios
        if self.admin_aberto():
            self.travar_admin()
        self.raiz.iconify()

    # ------------------------------------------------------------ avisos
    def verificar_bloqueios(self, trabalhos):
        maior = max((t["id"] for t in trabalhos), default=0)
        if self.ultimo_id is not None:
            novos = [t for t in trabalhos if t["id"] > self.ultimo_id and t["situacao"] == "bloqueado"]
            if novos:
                self.avisar_bloqueio(novos[0])
        self.ultimo_id = max(self.ultimo_id or 0, maior)

    def avisar_bloqueio(self, t):
        self.mostrar_janela()
        if self.alerta and self.alerta.winfo_exists():
            self.alerta.destroy()
        a = self.alerta = self.janela("Impressão não realizada", "vermelho")
        a.attributes("-topmost", True)
        self.texto(a, f"“{t['titulo'] or 'Documento'}”", pady=(8, 2))
        self.texto(a, t["motivo"] or "Você não tem folhas suficientes hoje.")
        self.texto(a, "Nada foi impresso. Tente um documento menor ou procure o suporte.",
                   "peq", "suave", pady=(8, 12))
        ttk.Button(a, text="Entendi", command=a.destroy).pack(anchor="e")
        a.bind("<Return>", lambda e: a.destroy())
        self.centralizar(a)

    # ------------------------------------------------------------ administrador
    # Abre numa janela própria, que já nasce do tamanho certo. Dentro do painel ela
    # dependia de a janela crescer, e no Zorin a janela ficava cortada.
    def montar_admin(self):
        rodape = self.linha(self.raiz, "fundo")
        rodape.pack(padx=14, pady=(0, 6))
        botao = tk.Label(rodape, text="⚙ Administrador", font=self.fontes["peq"],
                         bg=CORES["fundo"], fg="#8c959f", cursor="hand2")
        botao.pack(side="right")
        botao.bind("<Button-1>", lambda e: self.abrir_admin())
        self.janela_admin = None

    def admin_aberto(self):
        return self.janela_admin is not None and self.janela_admin.winfo_exists()

    def nova_janela_admin(self):
        j = self.janela_admin = self.janela("Administrador")
        j.protocol("WM_DELETE_WINDOW", self.travar_admin)
        return j

    def abrir_admin(self):
        if self.admin_aberto():
            return self.janela_admin.lift()
        j = self.nova_janela_admin()
        self.texto(j, "Senha do administrador", "peq", "suave", pady=(10, 2))
        linha = self.linha(j)
        campo = ttk.Entry(linha, show="•", width=22)
        campo.pack(side="left", fill="x", expand=True)
        msg = self.texto(j, fonte="peq", cor="vermelho", pady=(4, 0))

        def entrar(_=None):
            senha = campo.get()
            campo.delete(0, "end")
            if not senha:
                return msg.config(text="Digite a senha.")
            try:
                dados = self.servico.enviar("/login", {"senha": senha})
            except ErroAPI as e:
                return msg.config(text=str(e))
            self.senha_admin = senha
            j.destroy()
            self.montar_opcoes(dados)

        campo.bind("<Return>", entrar)
        ttk.Button(linha, text="Entrar", command=entrar).pack(side="left", padx=(8, 0))
        self.centralizar(j)
        campo.focus_set()

    def montar_opcoes(self, dados):
        o = self.nova_janela_admin()
        self.secao(o, "Controle de impressão")
        self.lbl_estado = self.texto(o, fonte="peq")
        self.btn_controle = ttk.Button(o, text="Desativar controle", command=self.alternar_controle)
        self.btn_controle.pack(anchor="w", pady=(4, 0))

        self.secao(o, "Folhas por dia (todos os usuários)")
        linha = self.linha(o)
        self.spin_padrao = ttk.Spinbox(linha, from_=0, to=1000, width=6)
        self.spin_padrao.pack(side="left")
        ttk.Button(linha, text="Salvar", command=self.salvar_padrao).pack(side="left", padx=8)

        self.secao(o, "Limite de um usuário específico")
        grade = self.linha(o)
        for coluna, nome in enumerate(("Usuário", "Folhas")):
            tk.Label(grade, text=nome, font=self.fontes["peq"], bg=CORES["cartao"],
                     fg=CORES["suave"]).grid(row=0, column=coluna, sticky="w")
        self.cmb_usuario = ttk.Combobox(grade, width=14)
        self.cmb_usuario.grid(row=1, column=0, padx=(0, 8))
        self.cmb_usuario.bind("<<ComboboxSelected>>", lambda e: self.preencher_limite())
        self.spin_usuario = ttk.Spinbox(grade, from_=0, to=1000, width=6)
        self.spin_usuario.grid(row=1, column=1, padx=(0, 8))
        ttk.Button(grade, text="Salvar", command=self.salvar_usuario).grid(row=1, column=2)
        ttk.Button(o, text="Voltar esse usuário ao padrão",
                   command=lambda: self.salvar_usuario(usar_padrao=True)).pack(anchor="w", pady=(6, 0))
        self.lbl_proprios = self.texto(o, fonte="peq", cor="suave", pady=(6, 0))
        self.lbl_msg = self.texto(o, fonte="peq", pady=(6, 0))
        self.lbl_msg.config(height=2)  # espaço reservado: a janela não precisa crescer

        tk.Frame(o, height=1, bg=CORES["borda"]).pack(fill="x", pady=(10, 8))
        ttk.Button(o, text="Sair", command=self.travar_admin).pack(anchor="e")
        self.mostrar_admin(dados)
        self.renovar_trava()
        self.centralizar(o)

    def travar_admin(self):
        self.senha_admin = None
        if self.timer_trava:
            self.raiz.after_cancel(self.timer_trava)
            self.timer_trava = None
        if self.admin_aberto():
            self.janela_admin.destroy()
        self.janela_admin = None

    def renovar_trava(self):
        if self.timer_trava:
            self.raiz.after_cancel(self.timer_trava)
        self.timer_trava = self.raiz.after(TRAVAR_ADMIN_MS, self.travar_admin)

    def mostrar_admin(self, d):
        self.mostrar_estado(d)
        self.spin_padrao.set(d["limite_padrao"])
        self.limites = d["limites_proprios"]
        self.cmb_usuario["values"] = sorted(set(self.limites) | {self.usuario.lower()})
        lista = ", ".join(f"{u} ({n})" for u, n in self.limites.items())
        self.lbl_proprios.config(text=f"Com limite próprio: {lista}" if lista
                                 else "Todos usam o limite padrão.")
        self.preencher_limite()

    def preencher_limite(self):
        usuario = self.cmb_usuario.get().strip().lower()
        if usuario:
            self.spin_usuario.set(self.limites.get(usuario, self.spin_padrao.get()))

    def acao_admin(self, caminho, dados, sucesso):
        self.renovar_trava()
        try:
            resposta = self.servico.enviar(caminho, dict(dados, senha=self.senha_admin))
        except ErroAPI as e:
            return self.lbl_msg.config(text=str(e), fg=CORES["vermelho"])
        self.mostrar_admin(resposta)
        self.lbl_msg.config(text=sucesso, fg=CORES["verde"])
        self.atualizar_agora()

    def alternar_controle(self):
        ativar = not self.controle_ativo
        self.acao_admin("/controle", {"ativo": ativar}, "Controle reativado." if ativar else
                        "Controle desativado: as impressões não serão descontadas.")

    def salvar_padrao(self):
        valor = self.spin_padrao.get()
        self.acao_admin("/limite", {"limite": valor},
                        f"Pronto: {valor} folha(s) por dia para quem não tem limite próprio.")

    def salvar_usuario(self, usar_padrao=False):
        usuario = self.cmb_usuario.get().strip()
        if not usuario:
            return self.lbl_msg.config(text="Escreva ou escolha o nome do usuário.",
                                       fg=CORES["vermelho"])
        if usar_padrao:
            self.acao_admin("/limite", {"usuario": usuario, "usar_padrao": True},
                            f"Pronto: {usuario} voltou ao limite padrão.")
        else:
            valor = self.spin_usuario.get()
            self.acao_admin("/limite", {"usuario": usuario, "limite": valor},
                            f"Pronto: {usuario} pode imprimir {valor} folha(s) por dia.")


def criar_icone_linux():
    """Copia o atalho para a Área de Trabalho do usuário no primeiro login."""
    origem = Path("/usr/share/applications/cotas-painel.desktop")
    try:
        pasta = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True,
                               text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pasta = str(Path.home() / "Desktop")
    destino = Path(pasta) / origem.name
    if not origem.exists() or destino.exists() or not destino.parent.is_dir() \
            or destino.parent == Path.home():
        return
    destino.write_bytes(origem.read_bytes())
    destino.chmod(0o755)
    subprocess.run(["gio", "set", str(destino), "metadata::trusted", "true"], capture_output=True)


def registrar_erro():
    # pythonw não tem console: sem isto, um erro fecharia o painel em silêncio
    arquivo = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "cotas_painel_erro.log"
    try:
        with open(arquivo, "a", encoding="utf-8") as f:
            f.write(traceback.format_exc() + "\n")
        raiz = tk.Tk()
        raiz.withdraw()
        messagebox.showerror("Cota de impressão", f"O painel não conseguiu abrir.\nDetalhes em: {arquivo}")
        raiz.destroy()
    except Exception:
        pass


def main():
    try:
        if config.WINDOWS:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except (AttributeError, OSError):
                pass
        usuario = getpass.getuser()
        trava = instancia_unica(usuario)
        if not trava:
            return
        if not config.WINDOWS:
            try:
                criar_icone_linux()
            except OSError:
                pass
        raiz = tk.Tk()
        Painel(raiz, usuario, Servico(config.carregar()["porta_api"], usuario), trava)
        if "--minimizado" in sys.argv:
            raiz.iconify()
        raiz.mainloop()
    except Exception:
        registrar_erro()
