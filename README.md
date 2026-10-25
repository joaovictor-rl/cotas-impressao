# Cotas de Impressão

Controle de cotas de impressão por usuário para Linux (CUPS).

## Por que existe

Na reprografia da **Escola de Música da UFPA**, muitas partituras são impressas e acabam
nem sendo usadas, gerando desperdício de papel e toner. O sistema dá a cada usuário do
computador uma **cota diária de folhas**: se o documento passar do saldo, não é impresso.

## Como funciona

- Cada usuário tem a sua cota, que renova à meia-noite.
- A conta é em folhas: considera cópias, frente e verso e páginas por folha.
- O controle é um backend do CUPS que fica entre a fila e a impressora.

## Uso

Tudo é feito pela interface do programa, pelo ícone **Cota de Impressão**:

- **Usuário:** vê quantas folhas restam no dia e as últimas impressões.
- **Administrador:** clica em **⚙ Administrador**, digita a senha e pode:
  - mudar o limite diário de todos;
  - dar um limite próprio a um usuário (ex.: um professor que precisa imprimir mais);
  - desativar e reativar o controle temporariamente.

As alterações ficam salvas mesmo depois de reiniciar o computador.

## Instalação

Uma vez por computador, vale para todos os usuários (inclusive os novos):

```bash
bash INSTALAR_LINUX.sh
```

Para desinstalar: `bash DESINSTALAR_LINUX.sh`. Se algo der errado: `bash REPARAR_LINUX.sh`.

## Administração pelo terminal (opcional)

```text
sudo cotas-admin listar              saldo de todos hoje
sudo cotas-admin limite maria 30     limite próprio para um usuário
sudo cotas-admin extra maria 5       folhas extras só hoje
sudo cotas-admin diagnostico         verifica serviço, impressoras e fila
```

## Situação atual

Em uso no laboratório da Escola de Música da UFPA (Zorin OS).

Próximo passo: gráficos do que cada usuário imprimiu, para acompanhar o consumo e produzir
relatórios sobre o que foi impresso.
