/* Cabe no Bolso · demo web (Protótipo do Time 05)
 * Sem framework, sem build. Fala com a API em /api/... ; com ?mock=1 (ou se /api/saude falhar) usa demo/mock/*.json.
 * O que a interface impõe: nenhum número é calculado aqui; todo número exibido carrega data-origem, resolvida a partir
 * de numeros_validados que a API devolve (o mesmo conjunto que o guardião usa); a expressão de condição proibida nunca aparece
 * (vira "depende de aprovação"); a IA se identifica como IA e sempre oferece uma pessoa.
 * Os `dados` dos cards são os dicts do cabe_core (capacidade.motor, ofertas.montar, ofertas.plano_de, acompanhar.ciclo, painel.juri).
 */
(function () {
  'use strict';

  // ---------- personas da demo (clientes reais da base; datas em que as regras fecham sem olhar o futuro) ----------
  const PERSONAS = {
    ana: { chave: 'ana', cliente_id: '755627ab-804b-4211-b0ea-f4ebacc58716', anomes: 202508, rotulo: 'Ana · Escorregão' },
    bruno: { chave: 'bruno', cliente_id: '3e7d20b2-4c4f-450a-bbd2-e60bfda81f0b', anomes: 202509, rotulo: 'Bruno · Rolando' },
  };
  const MESES = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];
  const ACOES_ANALISE = ['ver_opcoes', 'consigo_pagar', 'por_que_alta'];
  const CHIPS_PADRAO = [
    { rotulo: 'Ver opções', acao: 'ver_opcoes' },
    { rotulo: 'Consigo pagar minha fatura?', acao: 'consigo_pagar' },
    { rotulo: 'Por que veio tão alta?', acao: 'por_que_alta' },
    { rotulo: 'Falar com uma pessoa', acao: 'falar_com_pessoa', secundario: true },
  ];
  const ROTULO_ACAO = {
    ver_opcoes: 'Ver opções', consigo_pagar: 'Consigo pagar minha fatura?', por_que_alta: 'Por que minha fatura veio tão alta?',
    confirmar: 'Quero essa opção', falar_com_pessoa: 'Quero falar com uma pessoa', nao_quero: 'Prefiro continuar como está',
    pagar_minimo: 'Vou pagar o mínimo', pagar_outro_valor: 'Vou pagar outro valor',
  };
  const MOTIVOS_DISLIKE = ['não entendi', 'não é o que preciso', 'insistente', 'errou meus números'];
  const ERRO_PADRAO = 'Não consegui ler seu extrato agora. Posso tentar de novo ou te passar para uma pessoa.';

  // ---------- estado da sessão (espelha o state do agente; nada é calculado aqui) ----------
  let estado;
  function novoEstado(persona) {
    return {
      persona, modo: null, saude: null, api: null,
      sessao: null, consentimento: null, registroConsentimento: null, insight: null,
      escolha: null, pagamentoSimulado: null, intercepto: null,
      chat: { itens: [], sugestoes: [], analisou: false, ocupado: false, encerrado: false },
      plano: { confirmado: false, dados: null, opcao: null, teto: null, resumo: '', ciclos: [], encerrado: false },
      motor: null, ofertas: null,
      numeros: new Map(), guardiao: { removidos: 0, termos: 0 }, feedback: [], etapas: new Set(),
      trace: [], painel: null, tela: 'cartao', painelAba: 'trace',
    };
  }

  // ---------- utilidades ----------
  const $ = (sel) => document.querySelector(sel);
  function h(tag, attrs, ...filhos) {
    const el = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v == null || v === false) continue;
        if (k === 'class') el.className = v;
        else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
        else el.setAttribute(k, v === true ? '' : v);
      }
    }
    for (const f of filhos.flat()) {
      if (f == null || f === false) continue;
      el.append(f.nodeType ? f : document.createTextNode(String(f)));
    }
    return el;
  }
  const espera = (ms) => new Promise((r) => setTimeout(r, ms));
  const clone = (o) => JSON.parse(JSON.stringify(o));
  function brl(centavos) {
    const v = Math.abs(centavos) / 100;
    const s = v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return (centavos < 0 ? '-' : '') + 'R$ ' + s;
  }
  function mesNome(anomes) {
    if (!anomes) return '—';
    return MESES[(anomes % 100) - 1] + '/' + Math.floor(anomes / 100);
  }
  function pct(taxa) {
    return (taxa * 100).toLocaleString('pt-BR', { maximumFractionDigits: 2 }) + '% ao mês';
  }
  function sanear(texto) {
    return String(texto || '').replace(/sujeito[s]? a/gi, 'depende de aprovação');
  }
  const valorDe = (x) => (x != null && typeof x === 'object') ? x.valor : x;

  /** Registro de números validados: valor -> conjunto de origens (o mesmo conjunto que o guardião usa). */
  function registrar(lista) {
    for (const n of lista || []) {
      if (!n || typeof n.valor !== 'number' || !n.origem) continue;
      if (!estado.numeros.has(n.valor)) estado.numeros.set(n.valor, new Set());
      estado.numeros.get(n.valor).add(n.origem);
    }
  }
  function origemDe(valor, dica) {
    const set = estado.numeros.get(valor);
    if (!set || !set.size) return 'sem-origem';
    const lista = Array.from(set);
    if (dica) {
      const pref = lista.find((o) => o.toLowerCase().includes(dica));
      if (pref) return pref;
    }
    return lista[0];
  }

  /** Número com origem. Aceita inteiro do núcleo (origem resolvida pelo registro) ou {valor, origem}. Nunca calcula. */
  function num(item, opts) {
    opts = opts || {};
    const valor = valorDe(item);
    if (typeof valor !== 'number') return h('span', { class: 'num', 'data-origem': 'sem-origem' }, '—');
    const unidade = opts.unidade || 'centavos';
    let texto;
    if (unidade === 'centavos') texto = brl(valor);
    else if (unidade === '%') texto = valor + '%';
    else if (unidade === 'dia') texto = 'dia ' + valor;
    else if (unidade === 'dias') texto = valor + (valor === 1 ? ' dia' : ' dias');
    else if (unidade === 'parcelas') texto = valor + (valor === 1 ? ' parcela' : ' parcelas');
    else if (unidade === 'faturas') texto = valor + (valor === 1 ? ' fatura' : ' faturas');
    else texto = String(valor);
    if (opts.so) texto = String(valor);
    const origem = (item && item.origem) || origemDe(valor, opts.dica || (unidade !== 'centavos' ? unidade.replace('s', '') : null));
    return h('span', { class: 'num' + (opts.classe ? ' ' + opts.classe : ''), 'data-origem': origem, title: 'origem: ' + origem }, texto);
  }

  /** Marca os números de um texto do agente com a origem registrada (a cadeia de rastreabilidade chega ao texto). */
  function marcarTexto(texto) {
    const frag = document.createDocumentFragment();
    const re = /(R\$\s?\d{1,3}(?:\.\d{3})*,\d{2})|(\bdia\s\d{1,2}\b)|(\b\d{1,3}\sdias?\b)|(\b\d{1,3}\sparcelas?\b)|(\b\d{1,3}%)/g;
    let ultimo = 0, m;
    texto = sanear(texto);
    while ((m = re.exec(texto))) {
      if (m.index > ultimo) frag.append(texto.slice(ultimo, m.index));
      let valor, dica;
      if (m[1]) { valor = Math.round(parseFloat(m[1].replace(/R\$\s?/, '').replace(/\./g, '').replace(',', '.')) * 100); dica = null; }
      else if (m[2]) { valor = parseInt(m[2].replace(/\D/g, ''), 10); dica = 'dia'; }
      else if (m[3]) { valor = parseInt(m[3], 10); dica = 'dias'; }
      else if (m[4]) { valor = parseInt(m[4], 10); dica = 'parcela'; }
      else { valor = parseInt(m[5], 10); dica = 'pct'; }
      const origem = origemDe(valor, dica);
      frag.append(h('span', { class: 'num', 'data-origem': origem, title: 'origem: ' + origem }, m[0]));
      ultimo = m.index + m[0].length;
    }
    if (ultimo < texto.length) frag.append(texto.slice(ultimo));
    return frag;
  }

  function avisar(texto, ms) {
    const el = $('#aviso-global');
    el.textContent = texto;
    el.hidden = false;
    clearTimeout(avisar.t);
    avisar.t = setTimeout(() => { el.hidden = true; }, ms || 5000);
  }

  // ---------- adaptadores de dados: API real e mock, mesma interface ----------
  async function fetchJson(url, opts, timeoutMs) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), timeoutMs || 25000);
    try {
      const r = await fetch(url, Object.assign({ signal: ctl.signal, cache: 'no-store' }, opts || {}));
      const corpo = await r.json().catch(() => ({}));
      if (!r.ok) {
        const e = new Error(corpo.mensagem_cliente || corpo.erro || ('HTTP ' + r.status));
        e.mensagem_cliente = corpo.mensagem_cliente;
        throw e;
      }
      return corpo;
    } finally { clearTimeout(t); }
  }
  const post = (url, body) => fetchJson(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

  class Api {
    constructor(saude) { this.saude_ = saude; }
    sessao(p) { return post('/api/sessao', { cliente_id: p.cliente_id, anomes: p.anomes, persona: p.chave }); }
    consentimento(concedido) { return post('/api/consentimento', { sessao_id: estado.sessao.sessao_id, concedido }); }
    mensagem(m) { return post('/api/mensagem', Object.assign({ sessao_id: estado.sessao.sessao_id }, m)); }
    avancarMes() { return post('/api/avancar-mes', { sessao_id: estado.sessao.sessao_id }); }
    trace() { return fetchJson('/api/trace/' + encodeURIComponent(estado.sessao.sessao_id)); }
    painel() { return fetchJson('/api/painel/' + encodeURIComponent(estado.sessao.sessao_id)); }
    saude() { return Promise.resolve(this.saude_); }
  }

  class Mock {
    constructor(persona) { this.persona = persona; this.dados = null; }
    async carregar() { this.dados = await fetchJson('mock/' + this.persona + '.json'); }
    async sessao() { await espera(300); return clone(this.dados.sessao); }
    async consentimento(concedido) { await espera(300); return clone(concedido ? this.dados.consentimento : this.dados.consentimento_negado); }
    async mensagem(m) {
      const chave = m.acao || 'texto';
      const precisaAnalise = ACOES_ANALISE.includes(chave) && !estado.chat.analisou;
      await espera(precisaAnalise ? 2800 : 600);
      let r = this.dados.mensagem[chave] || this.dados.mensagem.texto;
      if (r && r.por_escolha) r = r.por_escolha[(estado.escolha && estado.escolha.acao) || 'padrao'] || r.por_escolha.padrao;
      return clone(r);
    }
    async avancarMes() {
      await espera(700);
      const lista = this.dados.avancar_mes || [];
      const k = estado.plano.ciclos.length;
      if (!lista[k]) throw new Error('Não há mais meses gravados para esta persona.');
      return clone(lista[k]);
    }
    async trace() { return clone((this.dados.trace || []).filter((t) => estado.etapas.has(t.etapa))); }
    async painel() { return clone(this.dados.painel); }
    async saude() { return clone(this.dados.saude); }
  }

  async function detectarModo() {
    const params = new URLSearchParams(location.search);
    if (params.get('mock') === '1') return { modo: 'mock' };
    try {
      const s = await fetchJson('/api/saude', null, 2500);
      if (s && s.ok) return { modo: 'api', saude: s };
    } catch (e) { /* sem API: plano B com respostas gravadas */ }
    return { modo: 'mock' };
  }

  // ---------- navegação ----------
  function mostrar(tela) {
    estado.tela = tela;
    for (const sec of document.querySelectorAll('.tela')) sec.hidden = sec.id !== 'tela-' + tela;
    for (const b of document.querySelectorAll('.aba')) {
      const ativa = b.dataset.tela === tela || (tela === 'pagar' && b.dataset.tela === 'cartao');
      b.classList.toggle('ativa', ativa);
      if (ativa) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
    }
    const titulo = $('#tela-' + tela + ' .titulo');
    if (titulo) { titulo.setAttribute('tabindex', '-1'); titulo.focus({ preventScroll: true }); }
    window.scrollTo({ top: 0 });
    if (tela === 'banca') renderBanca();
    if (tela === 'plano') renderPlano();
    if (tela === 'chat') renderChat();
  }

  // ---------- inicialização ----------
  async function iniciar() {
    const persona = $('#persona').value;
    estado = novoEstado(persona);
    const pill = $('#modo-dados');
    pill.textContent = 'carregando';
    pill.className = 'pill';
    $('#cartao-conteudo').replaceChildren(h('p', { class: 'sub' }, 'Abrindo a sessão…'));
    $('#chat-mensagens').replaceChildren();
    mostrar('cartao');
    try {
      const det = await detectarModo();
      estado.modo = det.modo;
      if (det.modo === 'api') {
        estado.api = new Api(det.saude);
        estado.saude = det.saude;
        pill.textContent = 'API · dados ' + (det.saude.dados || '?') + (det.saude.modelo ? ' · ' + det.saude.modelo : '');
      } else {
        const mock = new Mock(persona);
        await mock.carregar();
        estado.api = mock;
        estado.saude = await mock.saude();
        pill.textContent = 'respostas gravadas (mock)';
        pill.classList.add('pill-atencao');
      }
      estado.sessao = await estado.api.sessao(PERSONAS[persona]);
      registrar(estado.sessao.numeros_validados);
      estado.consentimento = estado.sessao.consentimento === true ? true : null;
      estado.etapas.add('sessao');
      renderCartao();
    } catch (e) {
      $('#cartao-conteudo').replaceChildren(cardErro(e));
    }
  }

  function cardErro(e) {
    return h('div', { class: 'card erro' },
      h('h2', null, 'Não deu para continuar'),
      h('p', null, (e && e.mensagem_cliente) || ERRO_PADRAO),
      h('p', { class: 'nota' }, 'Detalhe técnico: ' + ((e && e.message) || 'desconhecido')),
      h('div', { class: 'acoes-linha' },
        h('button', { class: 'btn btn-primario', type: 'button', onclick: () => iniciar() }, 'Tentar de novo'),
        h('button', { class: 'btn btn-secundario', type: 'button', onclick: () => { location.search = '?mock=1'; } }, 'Usar respostas gravadas')));
  }

  // ---------- tela: cartão ----------
  function renderCartao() {
    const s = estado.sessao;
    const f = s.fatura;
    const filhos = [];

    if (estado.consentimento === null) {
      filhos.push(h('div', { class: 'card card-acento', role: 'group', 'aria-labelledby': 'consent-titulo' },
        h('h2', { id: 'consent-titulo' }, 'Análise do seu mês'),
        h('p', null, 'Quer que o ia.i analise seus últimos 90 dias de conta e cartão para mostrar se a fatura cabe no seu mês? Você pode desligar quando quiser.'),
        h('div', { class: 'acoes-linha' },
          h('button', { class: 'btn btn-primario', type: 'button', onclick: () => darConsentimento(true) }, 'Permitir'),
          h('button', { class: 'btn btn-secundario', type: 'button', onclick: () => darConsentimento(false) }, 'Agora não'))));
    }

    filhos.push(h('div', { class: 'card' },
      h('div', { class: 'fatura-topo' },
        h('span', { class: 'rotulo' }, 'Fatura fechada · ' + (s.mes_rotulo || mesNome(s.anomes))),
        h('span', { class: 'etiqueta etiqueta-neutra' }, 'simulação')),
      h('p', { class: 'sub' }, 'Olá, ' + (s.cliente && s.cliente.apelido ? s.cliente.apelido : 'cliente') + '.'),
      h('p', null, num(f.valor, { classe: 'num-grande', dica: 'fatura.valor' })),
      h('div', { class: 'grade-2' },
        h('div', null, h('span', { class: 'rotulo' }, 'Vence'), h('br'), num(f.vencimento_dia, { classe: 'num-medio', unidade: 'dia', dica: 'vencimento' })),
        h('div', null, h('span', { class: 'rotulo' }, 'Mínimo'), h('br'), num(f.minimo, { classe: 'num-medio', dica: 'minimo' }))),
      estado.consentimento === false ? h('p', { class: 'nota' }, 'Sem análise do orçamento. O pedido volta a aparecer no máximo uma vez por mês, nunca no meio do pagamento.') : null,
      h('div', { class: 'acoes' },
        h('button', { class: 'btn btn-primario btn-bloco', type: 'button', onclick: () => { renderPagar(); mostrar('pagar'); } }, 'Pagar fatura'))));

    if (estado.consentimento === true && estado.insight) {
      const i = estado.insight;
      const classe = { cabe: 'card-ok', falta_pontual: 'card-atencao', falta_que_se_repete: 'card-atencao', sem_credito: '', cobertura_em_andamento: 'card-ok' }[i.estado] || '';
      filhos.push(h('div', { class: 'card ' + classe },
        h('span', { class: 'rotulo' }, 'ia.i · pelo seu extrato'),
        h('p', { class: 'frase-motor' }, marcarTexto(i.texto)),
        i.botao && i.botao.rotulo ? h('div', { class: 'acoes' },
          h('button', { class: 'btn btn-secundario btn-bloco', type: 'button', onclick: () => abrirChat(i.botao.acao || 'ver_opcoes') }, i.botao.rotulo)) : null));
    }

    if (estado.pagamentoSimulado != null) {
      filhos.push(h('div', { class: 'card card-ok' },
        h('h2', null, 'Pagamento simulado registrado'),
        h('p', null, 'Valor: ', num(estado.pagamentoSimulado), '. Nenhum pagamento real foi feito.')));
    }
    $('#cartao-conteudo').replaceChildren(...filhos);
  }

  async function darConsentimento(concedido) {
    try {
      const r = await estado.api.consentimento(concedido);
      estado.consentimento = r.consentimento === true;
      estado.registroConsentimento = r.registro || null;
      registrar(r.numeros_validados);
      if (estado.consentimento) {
        estado.etapas.add('consentimento');
        estado.insight = r.insight || { estado: 'desconhecido', texto: 'Quer ver se a fatura cabe no seu mês?', botao: { rotulo: 'Ver no ia.i', acao: 'consigo_pagar' } };
      } else {
        estado.insight = null;
      }
      renderCartao();
    } catch (e) { avisar((e && e.mensagem_cliente) || ERRO_PADRAO); }
  }

  // ---------- tela: pagar fatura ----------
  function renderPagar() {
    const f = estado.sessao.fatura;
    const opcoes = f.opcoes_pagamento || [
      { rotulo: 'Total', valor: f.valor, acao: 'pagar_total' },
      { rotulo: 'Mínimo', valor: f.minimo, acao: 'pagar_minimo' },
      { rotulo: 'Outro valor', valor: null, acao: 'pagar_outro_valor' },
    ];
    const gravado = valorDe((opcoes.find((o) => o.acao === 'pagar_outro_valor') || {}).valor_gravado);
    const lista = h('div', { class: 'acoes', role: 'group', 'aria-label': 'Formas de pagar' });
    for (const o of opcoes) {
      const ativa = estado.escolha && estado.escolha.acao === o.acao;
      if (o.acao === 'pagar_outro_valor') {
        const input = h('input', { id: 'outro-valor', type: 'text', inputmode: 'decimal', placeholder: '0,00', 'aria-label': 'Outro valor em reais',
          value: gravado != null ? (gravado / 100).toFixed(2).replace('.', ',') : '', readonly: estado.modo === 'mock' && gravado != null ? true : null });
        lista.append(h('div', { class: 'escolha' + (ativa ? ' ativa' : '') },
          h('div', { style: 'flex:1' },
            h('div', null, o.rotulo),
            h('div', { class: 'campo' }, h('span', null, 'R$'), input,
              h('button', { class: 'btn btn-secundario btn-compacto', type: 'button', onclick: () => {
                const c = Math.round(parseFloat(String(input.value).replace(/\./g, '').replace(',', '.')) * 100);
                if (!c || c <= 0) { avisar('Digite um valor.'); return; }
                escolherPagamento('pagar_outro_valor', c);
              } }, 'Escolher')),
            estado.modo === 'mock' && gravado != null ? h('p', { class: 'nota' }, 'No modo gravado, o valor é o que o cliente pagou de verdade na base.') : null)));
      } else {
        lista.append(h('button', { class: 'escolha' + (ativa ? ' ativa' : ''), type: 'button', onclick: () => escolherPagamento(o.acao, valorDe(o.valor)) },
          h('span', null, o.rotulo), num(o.valor, { dica: o.acao === 'pagar_minimo' ? 'minimo' : 'fatura.valor' })));
      }
    }
    const filhos = [h('div', { class: 'card' },
      h('span', { class: 'rotulo' }, 'Fatura de ' + (estado.sessao.mes_rotulo || mesNome(estado.sessao.anomes))),
      h('p', null, num(f.valor, { classe: 'num-grande', dica: 'fatura.valor' })),
      h('p', { class: 'sub' }, 'Vence ', num(f.vencimento_dia, { unidade: 'dia', dica: 'vencimento' }), '. Escolha quanto pagar.'),
      lista)];
    if (estado.intercepto) filhos.push(renderCard(estado.intercepto, 'pagar'));
    if (estado.pagamentoSimulado != null) {
      filhos.push(h('div', { class: 'card card-ok' },
        h('h2', null, 'Pagamento simulado registrado'),
        h('p', null, 'Valor: ', num(estado.pagamentoSimulado), '. Nenhum pagamento real foi feito.'),
        h('div', { class: 'acoes' }, h('button', { class: 'btn btn-fantasma', type: 'button', onclick: () => mostrar('cartao') }, 'Voltar ao cartão'))));
    }
    $('#pagar-conteudo').replaceChildren(...filhos);
  }

  async function escolherPagamento(acao, valor) {
    estado.escolha = { acao, valor };
    estado.intercepto = null;
    estado.pagamentoSimulado = null;
    const total = valorDe(estado.sessao.fatura.valor);
    if (acao === 'pagar_total' || (valor != null && valor >= total)) {
      estado.pagamentoSimulado = valor != null ? valor : total;
      renderPagar();
      return;
    }
    if (estado.consentimento !== true) {
      // sem adesão: só as formas de pagar, sem análise nem oferta (spec: "sem adesão")
      estado.pagamentoSimulado = valor;
      renderPagar();
      return;
    }
    renderPagar();
    try {
      const r = await estado.api.mensagem({ acao, valor });
      registrar(r.numeros_validados);
      contarGuardiao(r.guardiao);
      const card = (r.cards || []).find((c) => c.tipo === 'insight' || c.tipo === 'aviso');
      if (card) {
        estado.intercepto = card;
        if (card.tipo === 'aviso') estado.pagamentoSimulado = valor;
      } else if (r.mensagens && r.mensagens.length) {
        estado.intercepto = { tipo: 'insight', dados: { texto: r.mensagens[0].texto, botao_primario: { rotulo: 'Ver opção', acao: 'ver_opcoes' }, botao_secundario: { rotulo: 'Continuar com este valor', acao: 'nao_quero' } } };
      } else {
        estado.pagamentoSimulado = valor;
      }
      renderPagar();
    } catch (e) { avisar((e && e.mensagem_cliente) || ERRO_PADRAO); }
  }

  async function continuarComEsteValor() {
    // cenário 3: a IA informa o custo uma vez e não insiste; o pagamento simulado segue
    const valor = estado.escolha && estado.escolha.valor;
    try {
      const r = await estado.api.mensagem({ acao: 'nao_quero' });
      registrar(r.numeros_validados);
      contarGuardiao(r.guardiao);
      const texto = (r.mensagens && r.mensagens[0] && r.mensagens[0].texto) || 'Tudo bem. Se mudar de ideia até o vencimento, é só me chamar.';
      estado.intercepto = { tipo: 'aviso', dados: { texto } };
      estado.chat.itens.push({ id: idNovo(), tipo: 'msg', papel: 'cliente', texto: 'Continuar com este valor' });
      for (const m of r.mensagens || []) estado.chat.itens.push({ id: idNovo(), tipo: 'msg', papel: m.papel || 'agente', texto: m.texto });
      estado.chat.encerrado = true;
      estado.chat.sugestoes = [];
      estado.pagamentoSimulado = valor;
      renderPagar();
    } catch (e) { avisar((e && e.mensagem_cliente) || ERRO_PADRAO); }
  }

  // ---------- chat ----------
  let contador = 0;
  const idNovo = () => 'i' + (++contador);

  function abrirChat(acao) {
    mostrar('chat');
    if (acao) enviarAcao(acao);
  }

  function contarGuardiao(g) {
    if (!g) return;
    estado.guardiao.removidos += (g.removidos || []).length;
    estado.guardiao.termos += (g.termos_bloqueados || []).length;
  }

  async function enviarAcao(acao, texto, rotulo) {
    if (estado.chat.ocupado) return;
    if (estado.consentimento !== true && acao !== 'falar_com_pessoa') {
      estado.chat.itens.push({ id: idNovo(), tipo: 'msg', papel: 'agente', texto: 'Para olhar seu mês eu preciso da sua permissão. Ela fica no cartão, no topo da fatura. Sem ela, mostro só as formas de pagar.' });
      estado.chat.sugestoes = [{ rotulo: 'Voltar ao cartão', acao: 'ir_cartao' }, { rotulo: 'Falar com uma pessoa', acao: 'falar_com_pessoa', secundario: true }];
      renderChat();
      return;
    }
    if (acao === 'ir_cartao') { mostrar('cartao'); return; }
    if (acao === 'avancar_mes') { await avancarMes(); return; }
    if (acao === 'ver_opcoes' && estado.chat.analisou && estado.ofertas) {
      // já analisado: reexibe o comparador sem nova chamada ao modelo
      estado.chat.itens.push({ id: idNovo(), tipo: 'msg', papel: 'cliente', texto: ROTULO_ACAO[acao] });
      estado.chat.itens.push({ id: idNovo(), tipo: 'card', card: { tipo: 'comparador', dados: estado.ofertas } });
      estado.chat.sugestoes = sugestoesPadrao();
      renderChat();
      return;
    }
    const rotuloCliente = texto || rotulo || ROTULO_ACAO[acao] || acao;
    estado.chat.itens.push({ id: idNovo(), tipo: 'msg', papel: 'cliente', texto: rotuloCliente });
    estado.chat.ocupado = true;
    estado.chat.sugestoes = [];
    const analise = acao && ACOES_ANALISE.includes(acao) && !estado.chat.analisou;
    estado.chat.itens.push({ id: 'carregando', tipo: 'carregando', texto: analise ? 'analisando seus últimos 90 dias…' : 'pensando…' });
    renderChat();
    try {
      const r = await estado.api.mensagem(acao ? { acao } : { texto });
      estado.chat.itens = estado.chat.itens.filter((i) => i.id !== 'carregando');
      registrar(r.numeros_validados);
      contarGuardiao(r.guardiao);
      const msgs = (r.mensagens || []).filter((m) => m.papel !== 'cliente');
      const cards = r.cards || [];
      const n = Math.max(msgs.length, cards.length);
      for (let i = 0; i < n; i++) {
        if (msgs[i]) estado.chat.itens.push({ id: idNovo(), tipo: 'msg', papel: 'agente', texto: msgs[i].texto });
        if (cards[i]) {
          estado.chat.itens.push({ id: idNovo(), tipo: 'card', card: cards[i] });
          absorverCard(cards[i]);
        }
      }
      if (analise) { estado.chat.analisou = true; estado.etapas.add('analise'); }
      if (acao === 'confirmar' && cards.some((c) => c.tipo === 'confirmacao')) estado.etapas.add('confirmar');
      if (acao === 'falar_com_pessoa' || cards.some((c) => c.tipo === 'encaminhamento' && c.dados && c.dados.status)) estado.chat.encerrado = true;
      if (acao === 'nao_quero') estado.chat.encerrado = true;
      estado.chat.sugestoes = r.sugestoes || (estado.chat.encerrado ? [] : sugestoesPadrao());
    } catch (e) {
      estado.chat.itens = estado.chat.itens.filter((i) => i.id !== 'carregando');
      estado.chat.itens.push({ id: idNovo(), tipo: 'card', card: { tipo: 'aviso', dados: { texto: (e && e.mensagem_cliente) || ERRO_PADRAO, erro: true } } });
      estado.chat.sugestoes = [{ rotulo: 'Tentar de novo', acao: acao || 'ver_opcoes' }, { rotulo: 'Falar com uma pessoa', acao: 'falar_com_pessoa', secundario: true }];
    } finally {
      estado.chat.ocupado = false;
      renderChat();
    }
  }

  function sugestoesPadrao() {
    const lista = CHIPS_PADRAO.slice();
    if (estado.plano.confirmado && !estado.plano.encerrado) lista.unshift({ rotulo: 'Avançar um mês (demo)', acao: 'avancar_mes' });
    else if (estado.ofertas && estado.ofertas.recomendada != null && !estado.plano.confirmado) lista.unshift({ rotulo: 'Quero a opção recomendada', acao: 'confirmar' });
    return lista;
  }

  function absorverCard(card) {
    const d = card.dados || {};
    if (card.tipo === 'diagnostico' && !d.so_categorias) estado.motor = d;
    if (card.tipo === 'comparador') estado.ofertas = d;
    if (card.tipo === 'confirmacao') {
      estado.plano.confirmado = true;
      estado.plano.dados = d.plano || null;
      estado.plano.opcao = d.opcao || null;
      estado.plano.teto = d.teto_cartao_mes != null ? d.teto_cartao_mes : (d.plano ? d.plano.teto_cartao_mes : null);
      estado.plano.resumo = d.resumo || '';
    }
  }

  function renderChat() {
    const raiz = $('#chat-mensagens');
    const filhos = [];
    if (!estado.chat.itens.length) {
      filhos.push(h('div', { class: 'card' },
        h('p', null, 'Oi. Sou o ia.i, uma inteligência artificial do banco. Posso ver se a fatura cabe no seu mês, ou te passar para uma pessoa.')));
    }
    for (const it of estado.chat.itens) {
      if (it.tipo === 'msg') filhos.push(renderMsg(it));
      else if (it.tipo === 'card') filhos.push(h('div', { class: 'chat-card' }, renderCard(it.card, 'chat')));
      else if (it.tipo === 'carregando') filhos.push(h('div', { class: 'analisando', role: 'status' }, h('span', { class: 'pontos', 'aria-hidden': 'true' }, h('i'), h('i'), h('i')), it.texto));
    }
    raiz.replaceChildren(...filhos);
    const chips = estado.chat.itens.length ? estado.chat.sugestoes : sugestoesPadrao();
    $('#chat-sugestoes').replaceChildren(...(chips || []).map((c) => h('button', { class: 'chip' + (c.secundario ? ' chip-secundario' : ''), type: 'button', disabled: estado.chat.ocupado ? true : null,
      onclick: () => enviarAcao(c.acao, null, c.rotulo) }, c.rotulo)));
    $('#chat-form').hidden = estado.chat.encerrado;
    $('#chat-entrada').disabled = estado.chat.ocupado;
    if (raiz.lastElementChild) raiz.lastElementChild.scrollIntoView({ block: 'end', behavior: 'smooth' });
  }

  function renderMsg(it) {
    const agente = it.papel === 'agente';
    const el = h('div', { class: 'msg ' + (agente ? 'agente' : 'cliente') },
      h('div', { class: 'papel' }, agente ? 'ia.i · IA' : 'Você'),
      h('div', { class: 'texto' }, agente ? marcarTexto(it.texto) : sanear(it.texto)));
    if (agente) el.append(renderAvaliacao(it));
    return el;
  }

  function renderAvaliacao(it) {
    const fb = estado.feedback.find((f) => f.id === it.id);
    const wrap = h('div', { class: 'avaliacao', role: 'group', 'aria-label': 'Avaliar resposta' });
    wrap.append(
      h('button', { type: 'button', 'aria-label': 'Gostei', 'aria-pressed': fb && fb.tipo === 'like' ? 'true' : 'false', onclick: () => avaliar(it, 'like') }, '👍'),
      h('button', { type: 'button', 'aria-label': 'Não gostei', 'aria-pressed': fb && fb.tipo === 'dislike' ? 'true' : 'false', onclick: () => avaliar(it, 'dislike') }, '👎'));
    if (fb && fb.tipo === 'dislike') {
      const motivos = h('div', { class: 'motivos', role: 'group', 'aria-label': 'Motivo' });
      for (const m of MOTIVOS_DISLIKE) {
        motivos.append(h('button', { type: 'button', 'aria-pressed': fb.motivo === m ? 'true' : 'false', onclick: () => { fb.motivo = m; renderChat(); } }, m));
      }
      wrap.append(motivos);
      if (fb.motivo) wrap.append(h('span', { class: 'nota' }, 'Registrado: ' + fb.motivo + '.'));
    }
    return wrap;
  }

  function avaliar(it, tipo) {
    const i = estado.feedback.findIndex((f) => f.id === it.id);
    if (i >= 0 && estado.feedback[i].tipo === tipo) estado.feedback.splice(i, 1);
    else if (i >= 0) { estado.feedback[i].tipo = tipo; estado.feedback[i].motivo = null; }
    else estado.feedback.push({ id: it.id, tipo, motivo: null, texto: it.texto.slice(0, 60) });
    renderChat();
  }

  // ---------- cards (dados = dicts do cabe_core) ----------
  function renderCard(card, contexto) {
    const d = card.dados || {};
    switch (card.tipo) {
      case 'insight': return cardInsight(d, contexto);
      case 'diagnostico': return d.so_categorias ? cardCategorias(d) : cardDiagnostico(d);
      case 'comparador': return cardComparador(d);
      case 'confirmacao': return cardConfirmacao(d);
      case 'encaminhamento': return cardEncaminhamento(d);
      case 'acompanhamento': return cardAcompanhamento(d);
      case 'aviso':
      default:
        return h('div', { class: 'card ' + (d.erro ? 'erro' : 'card-atencao') }, h('span', { class: 'rotulo' }, 'ia.i'), h('p', null, marcarTexto(d.texto || '')));
    }
  }

  function cardInsight(d, contexto) {
    const acoes = [];
    if (d.botao_primario) acoes.push(h('button', { class: 'btn btn-primario', type: 'button', onclick: () => abrirChat(d.botao_primario.acao || 'ver_opcoes') }, d.botao_primario.rotulo));
    if (d.botao_secundario) acoes.push(h('button', { class: 'btn btn-secundario', type: 'button', onclick: () => (d.botao_secundario.acao === 'nao_quero' && contexto === 'pagar') ? continuarComEsteValor() : enviarAcao(d.botao_secundario.acao) }, d.botao_secundario.rotulo));
    return h('div', { class: 'card card-acento' },
      h('span', { class: 'rotulo' }, 'ia.i · antes de confirmar'),
      h('p', { class: 'frase-motor' }, marcarTexto(d.texto || '')),
      d.paga_agora != null ? h('p', { class: 'sub' }, 'Você escolheu pagar ', num(d.paga_agora), '.') : null,
      acoes.length ? h('div', { class: 'acoes' }, ...acoes) : null);
  }

  /** dados = capacidade.motor */
  function cardDiagnostico(d) {
    const ev = h('ul', { class: 'evidencias' });
    const li = (chave, valor) => ev.append(h('li', null, h('span', null, chave), h('span', null, valor)));
    const f = d.fatura || {};
    if (f.valor != null) li('Fatura', num(f.valor, { dica: 'fatura.valor' }));
    if (d.renda_recorrente != null) li('Entra todo mês', h('span', null, num(d.renda_recorrente, { dica: 'renda_recorrente' }), d.dia_recebimento != null ? h('span', { class: 'sub' }, ' · ', num(d.dia_recebimento, { unidade: 'dia', dica: 'dia_recebimento' })) : null));
    if (d.fixos != null) li('Contas fixas', num(d.fixos, { dica: 'fixos' }));
    if (d.essenciais != null) li('Essenciais em conta', num(d.essenciais, { dica: 'essenciais' }));
    if (d.folga != null) li('Sobra para a fatura', num(d.folga, { dica: 'folga' }));
    if (d.falta > 0) li('Falta', num(d.falta, { dica: 'falta' }));
    if (d.dias_ate_recebimento != null && !d.cabe) li('Dinheiro volta em', num(d.dias_ate_recebimento, { unidade: 'dias', dica: 'dias_ate' }));
    const flags = d.flags || {};
    const detalhes = [];
    if (Array.isArray(d.proximas_faturas) && d.proximas_faturas.length) {
      detalhes.push(h('h3', null, 'Próximas faturas (projeção)'));
      const ul = h('ul', { class: 'evidencias' });
      d.proximas_faturas.forEach((v, i) => ul.append(h('li', null, h('span', null, mesNome(somaMes(d.anomes, i + 1))), num(v, { dica: 'proximas_faturas[' + i + ']' }))));
      detalhes.push(ul, h('p', { class: 'nota' }, 'Projeção: 1,33 × compras no cartão. Não é a fatura fechada.'));
    }
    if ((flags.gastos_atipicos || []).length) {
      detalhes.push(h('h3', null, 'Fora do padrão neste mês'));
      const ul = h('ul', { class: 'evidencias' });
      for (const g of flags.gastos_atipicos) ul.append(h('li', null, h('span', null, g.categoria + ' · ' + mesNome(g.anomes)), h('span', null, num(g.valor, { dica: 'gastos_atipicos' }), g.mediana ? h('span', { class: 'sub' }, ' (antes ', num(g.mediana, { dica: 'mediana' }), ')') : h('span', { class: 'sub' }, ' (novo)'))));
      detalhes.push(ul);
    }
    if ((flags.entradas_esporadicas || []).length) {
      detalhes.push(h('h3', null, 'Entradas que não contam como renda'));
      const ul = h('ul', { class: 'evidencias' });
      for (const e of flags.entradas_esporadicas) ul.append(h('li', null, h('span', null, (e.micro || e.descricao || 'entrada') + (e.dia_tipico ? ' · dia ' + e.dia_tipico : '')), h('span', null, num(e.mediana_mensal != null ? e.mediana_mensal : e.valor, { dica: 'esporadicas' }), h('span', { class: 'sub' }, ' por mês'))));
      detalhes.push(ul, h('p', { class: 'nota' }, 'PIX e extras podem ser reembolso ou transferência própria; a conta só passa a contar com eles se você confirmar.'));
    }
    if (flags.renda_irregular) detalhes.push(h('p', { class: 'nota' }, h('span', { class: 'etiqueta etiqueta-atencao' }, 'renda irregular'), ' A previsão de recebimento não é confiável; sem oferta de crédito.'));
    return h('div', { class: 'card' },
      h('span', { class: 'rotulo' }, 'Pelo seu extrato · últimos 90 dias'),
      h('p', { class: 'frase-motor' }, marcarTexto(d.frase || '')),
      ev,
      detalhes.length ? h('details', { class: 'detalhes' }, h('summary', null, 'Ver detalhes'), ...detalhes) : null);
  }

  function somaMes(anomes, n) {
    if (!anomes) return null;
    const idx = Math.floor(anomes / 100) * 12 + (anomes % 100) - 1 + n;
    return Math.floor(idx / 12) * 100 + (idx % 12) + 1;
  }

  /** dados = {so_categorias, fatura, flags: {composicao_fatura, gastos_atipicos}} */
  function cardCategorias(d) {
    const comp = (d.flags || {}).composicao_fatura || {};
    const ul = h('ul', { class: 'evidencias' });
    for (const c of comp.categorias || []) ul.append(h('li', null, h('span', null, c.categoria), h('span', null, num(c.valor, { dica: 'composicao' }), h('span', { class: 'sub' }, ' · ', num(c.pct, { unidade: '%', dica: 'pct' })))));
    const pc = comp.parcelas_em_curso || {};
    if (pc.quantidade) ul.append(h('li', null, h('span', null, 'Parcelas em curso (' + pc.quantidade + ')'), num(pc.valor, { dica: 'parcelas_em_curso' })));
    return h('div', { class: 'card' },
      h('span', { class: 'rotulo' }, 'O que compôs esta fatura · compras de ' + mesNome(comp.anomes_compras)),
      comp.total_compras != null ? h('p', null, num(comp.total_compras, { classe: 'num-medio', dica: 'total_compras' }), comp.mediana_compras ? h('span', { class: 'sub' }, ' · mês típico ', num(comp.mediana_compras, { dica: 'mediana_compras' })) : null) : null,
      ul.childElementCount ? ul : h('p', { class: 'sub' }, 'Nada fora do padrão dos últimos três meses.'));
  }

  /** dados = ofertas.montar */
  function cardComparador(of) {
    const cont = of.continuar_no_rotativo || {};
    const permitidas = (of.opcoes || []).filter((o) => o.cabe !== false && o.liberado !== false && !(o.bloqueios || []).length);
    const colunas = permitidas.slice(0, 2);
    const cobertura = (o) => o.produto === 'cheque_especial' || (o.dias != null && o.n_parcelas === 1);
    const cab = h('tr', null, h('th', { scope: 'col' }, ''), h('th', { scope: 'col' }, 'Se continuar como está'));
    colunas.forEach((o, i) => cab.append(h('th', { scope: 'col', class: i === 0 ? 'col-rec' : '' },
      h('span', { class: 'etiqueta ' + (i === 0 ? '' : 'etiqueta-neutra') }, i === 0 ? 'Recomendada' : 'Alternativa'), h('br'), o.rotulo_cliente || o.produto,
      h('span', { class: 'taxa' }, pct(o.taxa_mes), ' · ', o.taxa_status === 'confirmada' ? 'taxa confirmada' : 'taxa ilustrativa (fonte BC)'))));
    const linha = (rotulo, contVal, fn) => {
      const tr = h('tr', null, h('th', { scope: 'row' }, rotulo), h('td', null, contVal));
      colunas.forEach((o, i) => tr.append(h('td', { class: i === 0 ? 'col-rec' : '' }, fn(o))));
      return tr;
    };
    const corpo = h('tbody', null,
      linha('Paga agora', h('span', null, num(of.pagar_agora, { dica: 'pagar_agora' }), h('span', { class: 'taxa' }, 'o que cabe no mês')), () => num(of.pagar_agora, { dica: 'pagar_agora' })),
      linha('Parcela ou dias', h('span', null, 'juros de ' + pct(cont.taxa_mes || 0.14) + ' sobre ', num(cont.valor, { dica: 'continuar_no_rotativo.valor' })),
        (o) => cobertura(o) ? h('span', null, num(o.dias, { unidade: 'dias', dica: 'dias' }), ' pelo limite da conta, cobrindo ', num(o.valor_financiado, { dica: 'valor_financiado' })) : h('span', null, num(o.parcela, { dica: 'parcela' }), ' por mês')),
      linha('Quantas', 'sem data para acabar', (o) => cobertura(o) ? h('span', null, '1 vez, até o salário', o.quitado_em ? ' (' + o.quitado_em + ')' : '') : num(o.n_parcelas, { unidade: 'parcelas', dica: 'n_parcelas' })),
      linha('Custo total', h('span', null, num(cont.custo_1_mes, { dica: 'custo_1_mes' }), ' no 1º mês; até ', num(cont.custo_ate_teto, { dica: 'custo_ate_teto' }), h('span', { class: 'taxa' }, 'teto legal de 100% (Lei 14.690/2023)')), (o) => num(o.custo_total, { dica: 'custo_total' })),
      linha('Termina em', 'sem data', (o) => mesNome(o.termina_em)),
      linha('Cabe no mês?', h('span', { class: 'nao' }, 'não'), (o) => o.cabe ? h('span', { class: 'sim' }, 'sim') : h('span', { class: 'nao' }, 'não')));
    const sm = cont.se_pagar_minimo;
    return h('div', { class: 'card card-acento' },
      h('span', { class: 'rotulo' }, 'Saídas lado a lado · da mais barata para a mais cara'),
      colunas.length ? h('div', { class: 'comparador' }, h('table', null, h('thead', null, cab), corpo)) : h('p', null, 'Nenhuma opção de crédito cabe no seu mês. Uma pessoa do time pode ajudar.'),
      sm ? h('p', { class: 'nota' }, 'Se pagar só o mínimo (', num(sm.paga_agora, { dica: 'se_pagar_minimo.paga_agora' }), '), ficam ', num(sm.nao_pago, { dica: 'se_pagar_minimo.nao_pago' }), ' para trás: ', num(sm.custo_1_mes, { dica: 'se_pagar_minimo.custo_1_mes' }), ' de juros no 1º mês.') : null,
      of.teto_cartao_mes != null && colunas.length && !cobertura(colunas[0]) ? h('p', { class: 'nota' }, 'Com a recomendada, até a próxima fatura cabem ', num(of.teto_cartao_mes, { dica: 'teto_cartao_mes' }), ' no cartão.') : null,
      h('p', { class: 'nota' }, 'Depende de aprovação do serviço de crédito. Nada é contratado nesta conversa.'));
  }

  /** dados = {resumo, plano (ofertas.plano_de), opcao, teto_cartao_mes, aviso} */
  function cardConfirmacao(d) {
    const p = d.plano || {};
    return h('div', { class: 'card card-ok' },
      h('span', { class: 'rotulo' }, 'Confirmação'),
      h('p', { class: 'frase-motor' }, marcarTexto(d.resumo || '')),
      p.rotulo_cliente ? h('p', { class: 'sub' }, p.rotulo_cliente + (p.taxa_mes ? ' · ' + pct(p.taxa_mes) : '') + (p.termina_em ? ' · termina em ' + mesNome(p.termina_em) : '')) : null,
      d.aviso ? h('p', { class: 'nota' }, sanear(d.aviso)) : null,
      h('p', null, h('span', { class: 'etiqueta etiqueta-atencao' }, 'nada é contratado'), ' ', h('span', { class: 'etiqueta etiqueta-neutra' }, 'depende de aprovação')),
      h('div', { class: 'acoes' },
        h('button', { class: 'btn btn-primario btn-bloco', type: 'button', onclick: () => { renderPlano(); mostrar('plano'); } }, 'Ver o plano')));
  }

  function cardEncaminhamento(d) {
    return h('div', { class: 'card card-atencao' },
      h('span', { class: 'rotulo' }, 'Uma pessoa vai assumir'),
      h('p', null, sanear(d.texto || '')),
      d.motivo ? h('p', { class: 'nota' }, 'Motivo: ' + sanear(d.motivo)) : null,
      d.status ? h('p', null, h('span', { class: 'etiqueta etiqueta-atencao' }, d.status)) : null);
  }

  function barraCabe(teto, referencia) {
    const t = valorDe(teto), ref = valorDe(referencia);
    if (t == null || !ref) return null;
    const frac = Math.max(0, Math.min(1, t / ref));
    const classe = frac < 0.2 ? 'barra-critico' : frac < 0.4 ? 'barra-atencao' : '';
    return h('div', { class: 'barra ' + classe },
      h('div', { class: 'barra-trilho', role: 'img', 'aria-label': 'Cabem ' + brl(t) + ' no cartão até a próxima fatura' },
        h('div', { class: 'barra-preenchido', style: 'width:' + Math.round(frac * 100) + '%' })),
      h('div', { class: 'barra-rotulo' }, h('span', null, t > 0 ? 'Cabe no cartão até a próxima fatura' : 'Neste mês a sobra vai toda para a fatura'), num(t, { dica: 'teto_cartao' })));
  }

  /** dados = acompanhar.ciclo */
  function cardAcompanhamento(c) {
    const ok = c.paga_inteira;
    const meta = c.meta_ciclos || 3;
    return h('div', { class: 'card ' + (ok ? 'card-ok' : 'card-critico') },
      h('div', { class: 'fatura-topo' }, h('span', { class: 'rotulo' }, 'Fatura de ' + mesNome(c.anomes)),
        h('span', { class: 'etiqueta ' + (ok ? 'etiqueta-ok' : 'etiqueta-critico') }, ok ? 'paga inteira' : 'paga abaixo do total')),
      h('p', null, num(c.fatura, { classe: 'num-medio', dica: 'ciclo:fatura' }), ' ', h('span', { class: 'sub' }, c.ciclos_ok != null ? h('span', null, num(c.ciclos_ok, { so: true, dica: 'ciclos_ok' }), ' de ' + meta) : null)),
      h('div', { class: 'evidencias' },
        c.limite_liberado != null ? h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Limite liberado no cartão'), h('span', { class: 'valor' }, num(c.limite_liberado, { dica: 'limite_liberado' }))) : null,
        c.juros_evitados_acumulados != null ? h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Juros evitados até aqui'), h('span', { class: 'valor' }, num(c.juros_evitados_acumulados, { dica: 'juros_evitados' }))) : null,
        c.proxima_parcela != null ? h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Próxima parcela'), h('span', { class: 'valor' }, c.proxima_parcela ? h('span', null, num(c.proxima_parcela, { dica: 'proxima_parcela' }), c.parcelas_restantes != null ? h('span', { class: 'sub' }, ' (' + c.parcelas_pagas + ' paga' + (c.parcelas_pagas === 1 ? '' : 's') + ', ' + c.parcelas_restantes + ' restante' + (c.parcelas_restantes === 1 ? '' : 's') + ')') : null) : 'nenhuma: limite coberto')) : null,
        c.real && c.real.juros ? h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Juros de conta que a base registra no mês'), h('span', { class: 'valor' }, num(c.real.juros, { dica: 'real.juros' }))) : null),
      barraCabe(c.teto_cartao, estado.motor && estado.motor.folga),
      h('p', { class: 'nota' }, (c.fonte_mes === 'base' ? 'Fatura e pagamento: reais, da base. ' : 'Fatura: projeção. ') + 'Plano aplicado: simulação, sem modelo de linguagem.'));
  }

  // ---------- tela: plano ----------
  function renderPlano() {
    const raiz = $('#plano-conteudo');
    const filhos = [];
    if (!estado.plano.confirmado) {
      filhos.push(h('div', { class: 'card' },
        h('p', null, 'Nenhum plano confirmado ainda. O plano nasce na conversa com o ia.i, depois da sua confirmação.'),
        h('div', { class: 'acoes' }, h('button', { class: 'btn btn-secundario', type: 'button', onclick: () => mostrar('chat') }, 'Ir para o ia.i'))));
      raiz.replaceChildren(...filhos);
      return;
    }
    const p = estado.plano.dados || {};
    const op = estado.plano.opcao || {};
    filhos.push(h('div', { class: 'card card-acento' },
      h('span', { class: 'rotulo' }, 'Plano confirmado · simulação'),
      h('p', { class: 'frase-motor' }, marcarTexto(estado.plano.resumo || '')),
      h('p', { class: 'sub' }, (p.rotulo_cliente || op.rotulo_cliente || '') + (p.taxa_mes || op.taxa_mes ? ' · ' + pct(p.taxa_mes || op.taxa_mes) + ((op.taxa_status || '') === 'confirmada' ? ' (taxa confirmada)' : ' (taxa ilustrativa, fonte BC)') : '')),
      h('p', null, h('span', { class: 'etiqueta etiqueta-atencao' }, 'nada é contratado'), ' ', h('span', { class: 'etiqueta etiqueta-neutra' }, 'depende de aprovação'))));

    const lt = h('div', { class: 'linha-tempo', role: 'list', 'aria-label': 'Linha do tempo do plano' });
    const inicio = p.anomes_inicio || (estado.motor && estado.motor.anomes) || (estado.sessao && estado.sessao.anomes);
    for (let i = 0; i < 3; i++) {
      const c = estado.plano.ciclos[i];
      const cls = c ? (c.paga_inteira ? 'inteira' : 'rolada') : 'futura';
      lt.append(h('div', { class: 'marco ' + cls, role: 'listitem' }, h('span', { class: 'ponto', 'aria-hidden': 'true' }),
        h('div', { class: 'mes' }, c ? mesNome(c.anomes) : mesNome(somaMes(inicio, i + 1))),
        h('div', { class: 'estado' }, c ? (c.paga_inteira ? 'inteira' : 'rolada') : 'futura')));
    }
    const podeAvancar = !estado.plano.encerrado && estado.plano.ciclos.length < 3;
    filhos.push(h('div', { class: 'card' },
      h('h2', null, 'Três faturas inteiras seguidas encerram o plano'),
      lt,
      !estado.plano.ciclos.length && estado.plano.teto != null ? barraCabe(estado.plano.teto, estado.motor && estado.motor.folga) : null,
      h('div', { class: 'acoes' },
        h('button', { class: 'btn btn-primario btn-bloco', type: 'button', disabled: podeAvancar ? null : true, onclick: () => avancarMes() },
          estado.plano.encerrado ? 'Plano encerrado' : 'Avançar um mês (demo)')),
      h('p', { class: 'nota' }, 'Acompanhamento sem modelo de linguagem: cálculo determinístico a cada fatura.')));

    for (const c of estado.plano.ciclos.slice().reverse()) filhos.push(cardAcompanhamento(c));

    if (estado.plano.encerrado) {
      const real = estado.painel && estado.painel.comparativo_real_2025;
      filhos.push(h('div', { class: 'card card-ok' },
        h('h2', null, 'Plano encerrado'),
        h('p', null, 'Três faturas inteiras seguidas. O ia.i para de acompanhar e volta só se houver um novo sinal.'),
        real ? h('div', { class: 'evidencias' },
          h('p', { class: 'nota' }, 'O que aconteceu de verdade em 2025, na base:'),
          h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Faturas roladas no ano'), h('span', { class: 'valor' }, num(real.faturas_roladas, { unidade: 'faturas', dica: 'faturas_roladas' }))),
          h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Juros do cartão nessas faturas'), h('span', { class: 'valor' }, num(real.juros_pagos, { dica: 'juros_pagos' }))),
          real.juros_pagos_total_ano != null ? h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Juros pagos no ano, com os de conta'), h('span', { class: 'valor' }, num(real.juros_pagos_total_ano, { dica: 'juros_pagos_total_ano' }))) : null) : null));
    }
    raiz.replaceChildren(...filhos);
  }

  async function avancarMes() {
    if (!estado.plano.confirmado) { avisar('Confirme uma opção no ia.i antes de avançar o mês.'); mostrar('chat'); return; }
    if (estado.plano.encerrado || estado.plano.ciclos.length >= 3) return;
    try {
      const r = await estado.api.avancarMes();
      registrar(r.numeros_validados);
      const ciclo = Object.assign({}, r, (r.cards && r.cards[0] && r.cards[0].dados) || {});
      estado.plano.ciclos.push(ciclo);
      estado.plano.encerrado = !!ciclo.encerrado;
      estado.etapas.add('avancar_mes_' + estado.plano.ciclos.length);
      const frase = r.mensagem || ciclo.frase;
      if (frase) estado.chat.itens.push({ id: idNovo(), tipo: 'msg', papel: 'agente', texto: frase });
      if (estado.plano.encerrado) {
        try { estado.painel = await estado.api.painel(); registrar(estado.painel.numeros_com_origem); } catch (e) { /* painel é opcional aqui */ }
      }
      estado.chat.sugestoes = sugestoesPadrao();
      renderPlano();
      mostrar('plano');
    } catch (e) { avisar((e && e.mensagem_cliente) || e.message || ERRO_PADRAO); }
  }

  // ---------- tela: banca ----------
  async function renderBanca() {
    const raiz = $('#banca-conteudo');
    for (const b of document.querySelectorAll('.sub-aba')) {
      const ativa = b.dataset.painel === estado.painelAba;
      b.classList.toggle('ativa', ativa);
      b.setAttribute('aria-selected', ativa ? 'true' : 'false');
    }
    raiz.replaceChildren(h('p', { class: 'sub' }, 'Carregando…'));
    try {
      if (estado.painelAba === 'trace') estado.trace = await estado.api.trace();
      else { estado.painel = await estado.api.painel(); registrar(estado.painel.numeros_com_origem); }
    } catch (e) {
      raiz.replaceChildren(h('div', { class: 'card erro' }, h('p', null, 'Não consegui carregar este painel: ' + ((e && e.message) || ''))));
      return;
    }
    const s = estado.sessao || {};
    const filhos = [h('div', { class: 'card' },
      h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Sessão'), h('span', { class: 'valor' }, s.sessao_id || '—')),
      h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Dados'), h('span', { class: 'valor' }, estado.modo === 'api' ? 'API · ' + (estado.saude.dados || '?') : 'respostas gravadas (mock)')),
      h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Modelo'), h('span', { class: 'valor' }, (estado.saude && estado.saude.modelo) || 'nenhum (mock)')),
      h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Persona'), h('span', { class: 'valor' }, PERSONAS[estado.persona].rotulo + (s.cliente && s.cliente.grupo_rotulo ? ' · grupo ' + s.cliente.grupo_rotulo : ''))),
      h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Consentimento'), h('span', { class: 'valor' }, estado.consentimento === true ? 'sim' : estado.consentimento === false ? 'recusado' : 'não pedido ainda')),
      estado.registroConsentimento ? h('p', { class: 'nota' }, 'Registro: ' + [estado.registroConsentimento.data, estado.registroConsentimento.versao_texto, estado.registroConsentimento.escopo].filter(Boolean).join(' · ')) : null)];
    if (estado.painelAba === 'trace') filhos.push(painelTrace());
    else if (estado.painelAba === 'juri') filhos.push(painelJuri());
    else filhos.push(painelFinops());
    raiz.replaceChildren(...filhos);
  }

  function painelTrace() {
    const lista = estado.trace || [];
    const el = h('div', { class: 'card' }, h('h2', null, 'Como cheguei aqui'),
      h('p', { class: 'sub' }, 'Cada ferramenta chamada, na ordem, com os números que ela devolveu e a origem de cada um.'));
    if (!lista.length) el.append(h('p', { class: 'nota' }, 'Ainda sem chamadas. Dê o consentimento e converse com o ia.i.'));
    for (const t of lista) {
      el.append(h('div', { class: 'trace-item' },
        h('div', { class: 'cabeca' }, h('span', { class: 'ferramenta' }, (t.ordem != null ? t.ordem + '. ' : '') + t.ferramenta),
          h('span', { class: 'meta' }, (t.duracao_ms != null ? t.duracao_ms + ' ms' : 'gravado') + (t.llm ? ' · LLM' : ' · sem LLM'))),
        t.resumo ? h('div', { class: 'resumo' }, sanear(t.resumo)) : null,
        t.argumentos ? h('div', { class: 'meta' }, 'argumentos: ' + JSON.stringify(t.argumentos)) : null,
        (t.numeros || []).length ? h('div', { class: 'numeros' }, ...t.numeros.map((n) => h('span', { title: n.origem, 'data-origem': n.origem }, formatoLivre(n) + ' ← ' + n.origem))) : null));
    }
    const nums = Array.from(estado.numeros.entries());
    el.append(h('h3', null, 'Números validados nesta sessão (' + nums.length + ')'),
      h('div', { class: 'lista-numeros' }, ...nums.map(([v, set]) => h('div', { class: 'linha' }, h('span', { class: 'num' }, String(v)), h('span', { class: 'meta' }, Array.from(set).join(' · '))))),
      h('p', { class: 'nota' }, 'Valores em centavos, dias, parcelas ou percentuais, como saem do núcleo. Na tela, todo número tem data-origem; número sem origem aparece sublinhado em vermelho.'));
    return el;
  }

  const CAMPOS_NAO_MONETARIOS = new Set(['dia_recebimento', 'vencimento_dia', 'dias', 'dias_ate_recebimento', 'n_parcelas', 'parcelas_pagas',
    'parcelas_restantes', 'roladas_12m', 'roladas_com_atual', 'roladas_seguidas', 'maior_sequencia', 'meses_considerados', 'ciclos_ok',
    'ciclos_total', 'meta_ciclos', 'pct', 'desvio_pct', 'quantidade', 'anomes', 'anomes_compras', 'anomes_inicio', 'termina_em', 'meses',
    'meses_fechados', 'inicio', 'fim', 'lancamentos', 'taxa_mes', 'variacao_max', 'vezes_acima_do_normal', 'fator_projecao',
    'ampliacao_limite_pct', 'prazo_referencia', 'dia_tipico', 'faturas_roladas', 'meses_na_base', 'custo_estimado', 'chamadas_llm']);
  function formatoLivre(n) {
    const campo = String(n.origem || '').split(':').pop().split('.').pop().replace(/\[\d+\]$/, '');
    if (CAMPOS_NAO_MONETARIOS.has(campo) || !Number.isInteger(n.valor)) return String(n.valor);
    return brl(n.valor);
  }

  function painelJuri() {
    const p = estado.painel || {};
    const real = p.comparativo_real_2025 || {};
    const pl = p.plano || {};
    const grupoRotulo = typeof p.grupo === 'string' ? p.grupo : (p.grupo && (p.grupo.rotulo || p.grupo.grupo));
    return h('div', { class: 'card' }, h('h2', null, 'Painel do júri'),
      p.fatura_paga_no_vencimento != null ? h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Fatura paga no vencimento'), h('span', { class: 'valor' }, typeof p.fatura_paga_no_vencimento === 'boolean' ? (p.fatura_paga_no_vencimento ? 'sim' : 'não') : num(p.fatura_paga_no_vencimento, { dica: 'fatura_paga' }))) : null,
      p.nao_pago != null ? h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Não pago com o plano'), h('span', { class: 'valor' }, num(p.nao_pago, { dica: 'nao_pago' }))) : null,
      p.juros_evitados != null ? h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Juros evitados'), h('span', { class: 'valor' }, num(p.juros_evitados, { dica: 'juros_evitados' }))) : null,
      pl.custo_total != null ? h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Custo do plano (' + (pl.produto || '') + ')'), h('span', { class: 'valor' }, num(pl.custo_total, { dica: 'custo_total' }))) : null,
      pl.parcela != null ? h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Parcela × quantas'), h('span', { class: 'valor' }, num(pl.parcela, { dica: 'parcela' }), ' × ', num(pl.n_parcelas, { so: true, dica: 'n_parcelas' }), pl.termina_em ? ' · termina em ' + mesNome(pl.termina_em) : '')) : null,
      p.ciclos_ok != null ? h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Faturas inteiras seguidas'), h('span', { class: 'valor' }, num(p.ciclos_ok, { so: true, dica: 'ciclos_ok' }), ' de 3', p.encerrado ? ' · encerrado' : '')) : null,
      grupoRotulo ? h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Grupo (flag em código) · tipo da falta'), h('span', { class: 'valor' }, grupoRotulo + (p.tipo_falta ? ' · ' + p.tipo_falta : ''))) : null,
      h('h3', null, 'Comparativo com 2025 real (base)'),
      real.faturas_roladas != null ? h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Faturas roladas no ano'), h('span', { class: 'valor' }, num(real.faturas_roladas, { unidade: 'faturas', dica: 'faturas_roladas' }))) : null,
      real.juros_pagos != null ? h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Juros do cartão nessas faturas'), h('span', { class: 'valor' }, num(real.juros_pagos, { dica: 'juros_pagos' }))) : null,
      real.juros_pagos_total_ano != null ? h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Juros pagos no ano, com os de conta'), h('span', { class: 'valor' }, num(real.juros_pagos_total_ano, { dica: 'total_ano' }))) : null,
      h('h3', null, 'O que é simulado'),
      h('ul', { class: 'evidencias' }, ...((p.simulado_lista || (Array.isArray(p.simulado) ? p.simulado : [])).map((s) => h('li', null, h('span', null, s))))),
      h('p', { class: 'nota' }, 'Real: cliente, extrato, faturas reconstruídas, juros e modos de pagamento da base.'));
  }

  function painelFinops() {
    const f = (estado.painel && estado.painel.finops) || {};
    const fmt = (v, suf) => v == null ? '—' : v.toLocaleString('pt-BR') + (suf || '');
    const el = h('div', { class: 'card' }, h('h2', null, 'FinOps e guardrails'),
      h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Chamadas ao modelo'), h('span', { class: 'valor num' }, fmt(f.chamadas_llm))),
      h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Tokens de entrada'), h('span', { class: 'valor num' }, fmt(f.tokens_entrada))),
      h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Tokens de saída'), h('span', { class: 'valor num' }, fmt(f.tokens_saida))),
      h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Latência p50 / p95'), h('span', { class: 'valor num' }, fmt(f.latencia_p50_ms, ' ms') + ' / ' + fmt(f.latencia_p95_ms, ' ms'))),
      h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Custo estimado'), h('span', { class: 'valor' }, f.custo_estimado == null ? 'sem preço com fonte em config' : String(f.custo_estimado))),
      f.nota ? h('p', { class: 'nota' }, f.nota) : null,
      h('h3', null, 'Guardião (after_model_callback)'),
      h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Números removidos'), h('span', { class: 'valor num' }, String(estado.guardiao.removidos))),
      h('div', { class: 'linha' }, h('span', { class: 'chave' }, 'Termos bloqueados'), h('span', { class: 'valor num' }, String(estado.guardiao.termos))),
      h('p', { class: 'nota' }, 'Acompanhamento mensal sem LLM. O modelo entra só na conversa; os números vêm do núcleo.'),
      h('h3', null, 'Feedback da conversa (' + estado.feedback.length + ')'));
    for (const fb of estado.feedback) el.append(h('div', { class: 'linha' }, h('span', { class: 'chave' }, fb.tipo === 'like' ? 'gostei' : 'não gostei' + (fb.motivo ? ' · ' + fb.motivo : '')), h('span', { class: 'valor sub' }, fb.texto + '…')));
    return el;
  }

  // ---------- eventos ----------
  document.addEventListener('DOMContentLoaded', () => {
    for (const b of document.querySelectorAll('.aba')) b.addEventListener('click', () => mostrar(b.dataset.tela));
    for (const b of document.querySelectorAll('.sub-aba')) b.addEventListener('click', () => { estado.painelAba = b.dataset.painel; renderBanca(); });
    $('#persona').addEventListener('change', iniciar);
    $('#reiniciar').addEventListener('click', iniciar);
    $('#chat-form').addEventListener('submit', (ev) => {
      ev.preventDefault();
      const input = $('#chat-entrada');
      const t = input.value.trim();
      if (!t) return;
      input.value = '';
      enviarAcao(null, t);
    });
    const params = new URLSearchParams(location.search);
    if (params.get('persona') && PERSONAS[params.get('persona')]) $('#persona').value = params.get('persona');
    iniciar();
  });

  // exposto para depuração pela banca (somente leitura)
  window.cabeNoBolso = { get estado() { return estado; } };
})();
