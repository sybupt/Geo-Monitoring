/* 对话式出题：浮动窗口，支持文字输入与语音输入（Web Speech API）。
   用户说一段要求（如"重点出价格和避坑的问题"），后端解析后生成
   维度问题集并入第 2 步的问题集，供全模型基线与规则学习使用。
   语音识别用浏览器原生 SpeechRecognition（Chrome/Edge 免费可用），
   不支持的浏览器自动隐藏麦克风并提示。 */
const ChatWidget = (() => {
  const st = { history: [], busy: false, opened: false, rec: null, listening: false };

  function el(id) { return document.getElementById(id); }

  function mount() {
    el("chat-fab").addEventListener("click", toggle);
    el("chat-close").addEventListener("click", toggle);
    el("chat-send").addEventListener("click", send);
    el("chat-text").addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") send();
    });
    el("qz-chat").addEventListener("click", () => { if (!st.opened) toggle(); });
    setupVoice();
  }

  function toggle() {
    const win = el("chat-win");
    st.opened = !st.opened;
    win.hidden = !st.opened;
    if (st.opened) {
      el("chat-sub").textContent = subText();
      if (!el("chat-msgs").children.length) greet();
      el("chat-text").focus();
    }
  }

  function subText() {
    const t = (G.targets || []).find((x) => x.item_id === G.state.target);
    return t ? `当前目标物：《${t.title}》 · 说出或输入你的要求` : "说出或输入你的要求";
  }

  function greet() {
    bubble("assistant",
      "你好，我是出题助手。直接用一句话告诉我你想测哪些方面，比如：\n" +
      "· 「重点出价格和避坑的问题」\n" +
      "· 「出几条关于售后的」（自定义关注点也可以）\n" +
      "· 「把所有维度都来一遍」\n" +
      "也可以点下面的麦克风直接说话。生成的问题会并入第 2 步的维度问题集。");
  }

  function bubble(role, text, extra) {
    const box = el("chat-msgs");
    const div = document.createElement("div");
    div.className = `msg msg-${role}`;
    const inner = document.createElement("div");
    inner.className = "msg-body";
    if (role === "assistant" && extra && extra.engine) {
      const tag = document.createElement("span");
      tag.className = "msg-engine";
      tag.textContent = extra.engine === "llm" ? "LLM 解析" : "规则解析";
      inner.appendChild(tag);
    }
    const span = document.createElement("span");
    span.innerHTML = G.esc(text).replace(/\n/g, "<br>");
    inner.appendChild(span);
    if (extra && extra.sets && extra.sets.length) {
      const list = document.createElement("div");
      list.className = "msg-sets";
      extra.sets.forEach((s) => {
        const row = document.createElement("div");
        row.className = "msg-set";
        const head = document.createElement("b");
        head.textContent = `${s.label}（新增 ${s.new} · 共 ${s.total} 条）`;
        row.appendChild(head);
        const ul = document.createElement("div");
        ul.className = "msg-qs";
        (s.new_questions || []).slice(0, 6).forEach((q) => {
          const i = document.createElement("i");
          i.textContent = q;
          ul.appendChild(i);
        });
        if ((s.new_questions || []).length > 6) {
          const m = document.createElement("span");
          m.className = "note";
          m.textContent = `…等 ${s.new_questions.length} 条新增`;
          ul.appendChild(m);
        }
        row.appendChild(ul);
        list.appendChild(row);
      });
      inner.appendChild(list);
    }
    div.appendChild(inner);
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
  }

  async function send() {
    if (st.busy) return;
    const input = el("chat-text");
    const text = input.value.trim();
    if (!text) return;
    if (!G.state.target) { G.toast("请先在顶部选择目标物", true); return; }
    input.value = "";
    bubble("user", text);
    st.busy = true;
    el("chat-send").disabled = true;
    el("chat-send").textContent = "生成中…";
    try {
      const d = await G.post("/api/questions/chat", {
        target_id: G.state.target,
        message: text,
        history: st.history,
      });
      st.history.push({ role: "user", content: text });
      st.history.push({ role: "assistant", content: d.reply });
      if (st.history.length > 12) st.history = st.history.slice(-12);
      bubble("assistant", d.reply, d);
      if (d.added > 0) {
        G.toast(`对话出题完成：新增 ${d.added} 条问法（${d.engine === "llm" ? "LLM 解析" : "规则解析"}）`);
        if (G.current === "questions" && G.views.questions) G.views.questions.refresh();
        G.loadProgress();
      }
    } catch (e) {
      bubble("assistant", `出错了：${e.message}`);
      G.toast(`对话出题失败：${e.message}`, true);
    } finally {
      st.busy = false;
      el("chat-send").disabled = false;
      el("chat-send").textContent = "发送";
      el("chat-text").focus();
    }
  }

  /* ---------------- 语音输入（Web Speech API） ---------------- */
  function setupVoice() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    const mic = el("chat-mic");
    if (!SR) {
      mic.style.display = "none";
      el("chat-note").textContent = "当前浏览器不支持语音识别，请用文字输入（Chrome/Edge 支持）";
      return;
    }
    const rec = new SR();
    rec.lang = "zh-CN";
    rec.interimResults = true;
    rec.continuous = false;
    let finalText = "";
    rec.onstart = () => {
      st.listening = true;
      mic.classList.add("listening");
      el("chat-note").textContent = "正在聆听…说完自动发送";
      el("chat-text").placeholder = "（语音识别中）";
    };
    rec.onresult = (ev) => {
      let interim = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        if (ev.results[i].isFinal) finalText += ev.results[i][0].transcript;
        else interim += ev.results[i][0].transcript;
      }
      el("chat-text").value = finalText || interim;
    };
    rec.onerror = (ev) => {
      st.listening = false;
      mic.classList.remove("listening");
      const why = ev.error === "not-allowed"
        ? "麦克风权限被拒绝，请在浏览器地址栏允许麦克风后重试"
        : `语音识别出错（${ev.error}），可用文字输入`;
      el("chat-note").textContent = why;
    };
    rec.onend = () => {
      st.listening = false;
      mic.classList.remove("listening");
      el("chat-text").placeholder = "例如：重点出价格和避坑的问题，再补几个送礼场景的";
      const text = (el("chat-text").value || "").trim();
      el("chat-note").textContent = "";
      if (text && !st.busy) send();   // 说完自动发送，形成真正的对话
    };
    mic.addEventListener("click", () => {
      if (st.listening) { rec.stop(); return; }
      finalText = "";
      try { rec.start(); } catch (e) { /* 已在运行时忽略 */ }
    });
    st.rec = rec;
  }

  return { mount };
})();

document.addEventListener("DOMContentLoaded", ChatWidget.mount);
