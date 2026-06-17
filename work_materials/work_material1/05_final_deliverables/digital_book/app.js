const state = {
  book: null,
  fontSize: Number(localStorage.getItem('reader.fontSize') || '17'),
  activeId: '',
  storageKey: 'digital_book',
  askIndex: []
};

async function loadBook() {
  const version = new URLSearchParams(window.location.search).get('v') || Date.now().toString();
  const response = await fetch(`digital_book.json?v=${version}`);
  state.book = await response.json();
  renderBook(state.book);
}

function renderBook(book) {
  state.storageKey = `digitalBook.${book.book_id || book.title || 'local'}`;
  document.title = book.title;
  document.getElementById('bookTitle').textContent = book.title;
  document.getElementById('bookMeta').textContent = `${book.metadata?.format || ''} · ${book.metadata?.generated_at || ''}`;
  document.documentElement.style.setProperty('--reader-font-size', `${state.fontSize}px`);
  renderToc(book);
  renderContent(book);
  state.askIndex = buildAskIndex(book);
  restoreReaderState();
  bindScrollTracking();
}

function renderToc(book) {
  const toc = document.getElementById('toc');
  toc.innerHTML = '';
  const plan = book.metadata?.book_plan;
  if (plan?.chapters?.length) {
    for (const chapter of plan.chapters) {
      toc.appendChild(tocChapter(chapter, false));
    }
    return;
  }
  for (const project of book.projects || []) {
    toc.appendChild(tocLink(project.title, project.project_id, 'project'));
    for (const task of project.tasks || []) {
      toc.appendChild(tocLink(task.title, task.task_id, 'task'));
    }
  }
}

function tocChapter(chapter, expanded) {
  const wrap = el('div', `toc-chapter${expanded ? '' : ' collapsed'}`);
  const button = el('button', 'toc-chapter-toggle');
  button.type = 'button';
  button.dataset.target = chapter.chapter_id;
  const icon = el('span', 'toc-chapter-icon');
  const label = el('span', '');
  label.textContent = `第${chapter.chapter_no}章 ${chapter.title}`;
  button.appendChild(icon);
  button.appendChild(label);
  const sectionList = el('div', 'toc-section-list');
  for (const section of chapter.sections || []) {
    sectionList.appendChild(tocLink(`${section.section_no} ${section.title}`, section.section_id || chapter.chapter_id, 'task toc-section-link'));
  }
  const syncIcon = () => {
    icon.textContent = wrap.classList.contains('collapsed') ? '▶' : '▼';
  };
  button.addEventListener('click', () => {
    wrap.classList.toggle('collapsed');
    syncIcon();
    setActiveSection(chapter.chapter_id);
    document.getElementById(chapter.chapter_id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  syncIcon();
  wrap.appendChild(button);
  wrap.appendChild(sectionList);
  return wrap;
}

function tocLink(text, id, className) {
  const link = document.createElement('a');
  link.href = `#${id}`;
  link.textContent = text;
  link.className = className;
  link.addEventListener('click', () => setActiveSection(id));
  return link;
}

function renderContent(book) {
  const root = document.getElementById('content');
  root.innerHTML = '';
  const outline = renderBookOutline(book);
  if (outline) root.appendChild(outline);
  const bookChapters = book.metadata?.book_plan?.chapters || [];
  for (const project of book.projects || []) {
    const chapterPlan = bookChapters.find((chapter) => chapter.chapter_id === project.project_id);
    const anchoredSections = new Set();
    const section = el('section', 'project');
    section.id = project.project_id;
    section.appendChild(heading('h2', project.title));
    section.appendChild(paragraph(project.project_intro));
    section.appendChild(listBlock('学习目标', project.learning_goals || []));
    section.appendChild(listBlock('能力图谱', project.ability_map || []));
    root.appendChild(section);

    for (const task of project.tasks || []) {
      const taskEl = el('section', 'task');
      taskEl.id = task.task_id;
      taskEl.appendChild(heading('h3', task.title));
      taskEl.appendChild(tagRow('知识点', task.knowledge_points || []));
      for (const block of task.blocks || []) {
        for (const sectionPlan of matchingChapterSections(chapterPlan, block, anchoredSections)) {
          taskEl.appendChild(sectionAnchor(sectionPlan.section_id));
          anchoredSections.add(sectionPlan.section_id);
        }
        taskEl.appendChild(renderBlock(block));
      }
      root.appendChild(taskEl);
    }
  }
}

function renderBlock(block) {
  const node = el('article', `block block-${block.type}`);
  node.id = block.block_id;
  node.appendChild(blockHeading(block.title));
  if (block.type === 'video') {
    const video = document.createElement('video');
    video.controls = true;
    video.preload = 'metadata';
    video.playsInline = true;
    video.src = block.src;
    if (block.poster) video.poster = block.poster;
    if (block.start_time) video.dataset.startTime = block.start_time;
    if (block.end_time) video.dataset.endTime = block.end_time;
    video.addEventListener('loadedmetadata', () => {
      const seconds = timeToSeconds(video.dataset.startTime || '');
      if (seconds > 0) video.currentTime = seconds;
    }, { once: true });
    video.addEventListener('timeupdate', () => {
      const endSeconds = timeToSeconds(video.dataset.endTime || '');
      if (endSeconds > 0 && video.currentTime >= endSeconds) {
        video.pause();
        video.currentTime = timeToSeconds(video.dataset.startTime || '');
      }
    });
    node.appendChild(video);
    node.appendChild(videoActions(video));
    node.appendChild(paragraph(`${block.start_time || ''} - ${block.end_time || ''}`));
  } else if (block.items && block.items.length) {
    node.appendChild(list(block.items));
  }
  if (block.markdown) {
    const markdown = el('div', 'markdown');
    markdown.innerHTML = renderMarkdown(block.markdown);
    node.appendChild(markdown);
  }
  return node;
}

function blockHeading(text) {
  const title = heading('h4', '');
  title.className = 'block-heading';
  const marker = el('span', 'block-marker block-heading-marker');
  marker.setAttribute('aria-hidden', 'true');
  const label = el('span', 'block-heading-label');
  label.textContent = text;
  title.appendChild(marker);
  title.appendChild(label);
  return title;
}

function videoActions(video) {
  const wrap = el('div', 'video-actions');
  const toggle = el('button', 'video-toggle');
  const status = el('span', 'video-status');
  toggle.type = 'button';
  const syncLabel = () => {
    toggle.textContent = video.paused ? '播放' : '暂停';
    if (!video.paused) status.textContent = '';
  };
  toggle.addEventListener('click', async () => {
    if (video.paused) {
      try {
        status.textContent = '';
        const startSeconds = timeToSeconds(video.dataset.startTime || '');
        const endSeconds = timeToSeconds(video.dataset.endTime || '');
        if (startSeconds > 0 && (video.currentTime < startSeconds || (endSeconds > 0 && video.currentTime >= endSeconds))) {
          video.currentTime = startSeconds;
        }
        await video.play();
      } catch (error) {
        status.textContent = '浏览器阻止了自动播放，请直接点击视频控件播放。';
      }
    } else {
      video.pause();
    }
    syncLabel();
  });
  video.addEventListener('play', syncLabel);
  video.addEventListener('pause', syncLabel);
  video.addEventListener('ended', syncLabel);
  syncLabel();
  wrap.appendChild(toggle);
  wrap.appendChild(status);
  return wrap;
}

function renderMarkdown(value) {
  const lines = String(value || '').replace(/\r\n?/g, '\n').split('\n');
  const html = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    const fence = line.match(/^```\s*([\w-]+)?\s*$/);
    if (fence) {
      const codeLines = [];
      index += 1;
      while (index < lines.length && !/^```\s*$/.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      const language = fence[1] ? ` language-${escapeHtml(fence[1])}` : '';
      html.push(`<pre><code class="${language.trim()}">${escapeHtml(codeLines.join('\n'))}</code></pre>`);
      continue;
    }

    if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
      html.push('<hr>');
      index += 1;
      continue;
    }

    const headingMatch = line.match(/^(#{1,6})\s+(.+)$/);
    if (headingMatch) {
      const level = headingMatch[1].length;
      html.push(`<h${level}>${renderInlineMarkdown(headingMatch[2].trim())}</h${level}>`);
      index += 1;
      continue;
    }

    if (/^>\s?/.test(line)) {
      const quoteLines = [];
      while (index < lines.length && /^>\s?/.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^>\s?/, ''));
        index += 1;
      }
      html.push(`<blockquote>${renderMarkdown(quoteLines.join('\n'))}</blockquote>`);
      continue;
    }

    if (isTableStart(lines, index)) {
      const tableLines = [lines[index], lines[index + 1]];
      index += 2;
      while (index < lines.length && isTableRow(lines[index])) {
        tableLines.push(lines[index]);
        index += 1;
      }
      html.push(renderMarkdownTable(tableLines));
      continue;
    }

    const listMatch = line.match(/^(\s*)([-*+] |\d+[.)]\s+)(.+)$/);
    if (listMatch) {
      const ordered = /\d/.test(listMatch[2][0]);
      const tag = ordered ? 'ol' : 'ul';
      const items = [];
      while (index < lines.length) {
        const itemMatch = lines[index].match(/^(\s*)([-*+] |\d+[.)]\s+)(.+)$/);
        if (!itemMatch || (/\d/.test(itemMatch[2][0]) !== ordered)) break;
        const itemLines = [itemMatch[3]];
        index += 1;
        while (index < lines.length && lines[index].trim() && !/^(\s*)([-*+] |\d+[.)]\s+)/.test(lines[index])) {
          itemLines.push(lines[index].trim());
          index += 1;
        }
        items.push(`<li>${renderInlineMarkdown(itemLines.join('\n')).replace(/\n/g, '<br>')}</li>`);
      }
      html.push(`<${tag}>${items.join('')}</${tag}>`);
      continue;
    }

    const paragraphLines = [line.trim()];
    index += 1;
    while (
      index < lines.length &&
      lines[index].trim() &&
      !/^```/.test(lines[index]) &&
      !/^(#{1,6})\s+/.test(lines[index]) &&
      !/^>\s?/.test(lines[index]) &&
      !/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(lines[index]) &&
      !/^(\s*)([-*+] |\d+[.)]\s+)/.test(lines[index]) &&
      !isTableStart(lines, index)
    ) {
      paragraphLines.push(lines[index].trim());
      index += 1;
    }
    html.push(`<p>${renderInlineMarkdown(paragraphLines.join('\n')).replace(/\n/g, '<br>')}</p>`);
  }
  return html.join('');
}

function renderInlineMarkdown(value) {
  const codeSpans = [];
  let text = escapeHtml(value).replace(/`([^`]+)`/g, (_match, code) => {
    const token = `%%CODE${codeSpans.length}%%`;
    codeSpans.push(`<code>${code}</code>`);
    return token;
  });
  text = text
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/__([^_]+)__/g, '<strong>$1</strong>')
    .replace(/~~([^~]+)~~/g, '<del>$1</del>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/(^|[^_])_([^_\n]+)_/g, '$1<em>$2</em>')
    .replace(/!\[([^\]]*)\]\(([^\s)]+)(?:\s+&quot;[^&]*&quot;)?\)/g, (_match, alt, rawUrl) => {
      const url = safeMarkdownUrl(rawUrl);
      return url ? `<img src="${url}" alt="${escapeHtml(alt)}">` : escapeHtml(alt);
    })
    .replace(/\[([^\]]+)\]\(([^\s)]+)(?:\s+&quot;[^&]*&quot;)?\)/g, (_match, label, rawUrl) => {
      const url = safeMarkdownUrl(rawUrl);
      return url ? `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>` : label;
    });
  return text.replace(/%%CODE(\d+)%%/g, (_match, index) => codeSpans[Number(index)] || '');
}

function isTableStart(lines, index) {
  return isTableRow(lines[index]) && index + 1 < lines.length && /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[index + 1] || '');
}

function isTableRow(line) {
  return /^\s*\|.*\|\s*$/.test(line || '');
}

function renderMarkdownTable(lines) {
  const headers = splitTableRow(lines[0]);
  const aligns = splitTableRow(lines[1]).map((cell) => {
    const value = cell.trim();
    if (value.startsWith(':') && value.endsWith(':')) return 'center';
    if (value.endsWith(':')) return 'right';
    return 'left';
  });
  const bodyRows = lines.slice(2).map(splitTableRow);
  const ths = headers.map((cell, index) => `<th style="text-align:${aligns[index] || 'left'}">${renderInlineMarkdown(cell.trim())}</th>`).join('');
  const rows = bodyRows.map((row) => {
    const tds = headers.map((_header, index) => `<td style="text-align:${aligns[index] || 'left'}">${renderInlineMarkdown((row[index] || '').trim())}</td>`).join('');
    return `<tr>${tds}</tr>`;
  }).join('');
  return `<table><thead><tr>${ths}</tr></thead><tbody>${rows}</tbody></table>`;
}

function splitTableRow(line) {
  return String(line || '').trim().replace(/^\|/, '').replace(/\|$/, '').split('|');
}

function safeMarkdownUrl(rawUrl) {
  const decoded = String(rawUrl || '').replace(/&amp;/g, '&').replace(/&quot;/g, '"').trim();
  if (!decoded || /^[a-z][a-z0-9+.-]*:/i.test(decoded) && !/^(https?:|mailto:)/i.test(decoded)) return '';
  return escapeHtml(decoded);
}

function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function buildAskIndex(book) {
  const rows = [];
  for (const project of book.projects || []) {
    for (const task of project.tasks || []) {
      for (const block of task.blocks || []) {
        const textParts = [
          project.title,
          task.title,
          block.title,
          block.markdown || '',
          ...(block.items || []),
        ];
        const text = textParts.join(' ').replace(/\s+/g, ' ').trim();
        if (!text) continue;
        rows.push({
          id: block.block_id,
          blockType: block.type || block.block_type || '',
          projectTitle: project.title,
          taskTitle: task.title,
          blockTitle: block.title,
          text,
          evidence: [],
        });
      }
    }
  }
  return rows;
}

async function answerQuestion(rawQuestion) {
  const answer = document.getElementById('askAnswer');
  const terms = tokenizeQuestion(rawQuestion);
  answer.innerHTML = '';
  if (!terms.length) {
    answer.textContent = '请输入要检索的知识点、操作步骤或学习问题。';
    return;
  }
  const results = state.askIndex
    .map((row) => ({ row, score: scoreAskRow(row, terms) }))
    .filter((item) => item.score > 0)
    .sort((left, right) => right.score - left.score);
  const focusedResults = preferAskResults(results, terms).slice(0, 3);
  if (!focusedResults.length) {
    answer.textContent = '暂未在本教材中找到直接相关内容。可以换一个知识点、操作词或学习问题再问。';
    return;
  }
  const remoteAnswered = await answerWithRemoteService(rawQuestion, focusedResults, answer);
  if (!remoteAnswered) {
    renderLocalAskResults(focusedResults, terms, answer);
  }
}

function matchingChapterSections(chapterPlan, block, anchoredSections) {
  if (!chapterPlan?.sections?.length || !block) return [];
  const title = normalizeText(block.title || '');
  const blockId = normalizeText(block.block_id || '');
  if (!title && !blockId) return [];
  const result = [];
  for (const section of chapterPlan.sections) {
    if (!section.section_id || anchoredSections.has(section.section_id)) continue;
    const candidates = [section.title, ...(section.knowledge_points || [])].map(normalizeText).filter(Boolean);
    if (candidates.some((item) => title.includes(item) || item.includes(title) || blockId.includes(item))) {
      result.push(section);
    }
  }
  return result;
}

function normalizeText(value) {
  return String(value || '').replace(/[\s\-_/：:，,。、《》()（）]/g, '').toLowerCase();
}

function sectionAnchor(id) {
  const anchor = el('span', 'section-anchor');
  anchor.id = id;
  return anchor;
}

function renderBookOutline(book) {
  const plan = book.metadata?.book_plan;
  if (!plan?.chapters?.length) return null;
  const section = el('section', 'book-outline');
  section.id = 'book_outline';
  section.appendChild(heading('h2', '教材大纲'));
  const chapterList = document.createElement('ol');
  for (const chapter of plan.chapters) {
    const chapterItem = document.createElement('li');
    chapterItem.appendChild(document.createTextNode(`第${chapter.chapter_no}章 ${chapter.title}`));
    const sectionList = document.createElement('ol');
    for (const item of chapter.sections || []) {
      const sectionItem = document.createElement('li');
      sectionItem.appendChild(document.createTextNode(`${item.section_no} ${item.title}`));
      const pointList = document.createElement('ol');
      for (const point of item.knowledge_points || []) {
        const pointItem = document.createElement('li');
        pointItem.textContent = point;
        pointList.appendChild(pointItem);
      }
      if (pointList.children.length) sectionItem.appendChild(pointList);
      sectionList.appendChild(sectionItem);
    }
    if (sectionList.children.length) chapterItem.appendChild(sectionList);
    chapterList.appendChild(chapterItem);
  }
  section.appendChild(chapterList);
  return section;
}

function renderLocalAskResults(results, terms, answer) {
  for (const result of results) {
    answer.appendChild(renderAskResult(result.row, terms));
  }
}

async function answerWithRemoteService(question, results, answer) {
  const endpoint = (window.DIGITAL_BOOK_ASK_ENDPOINT || '').trim();
  if (!endpoint) return false;
  answer.textContent = 'AI ask-book service is generating...';
  try {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(buildAskPayload(question, results)),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    renderRemoteAnswer(payload, answer);
    return true;
  } catch (error) {
    answer.innerHTML = '';
    const warning = el('div', 'ask-result');
    warning.appendChild(paragraph(`Remote ask-book service is unavailable. Falling back to local retrieval. ${error.message || error}`));
    answer.appendChild(warning);
    return false;
  }
}

function buildAskPayload(question, results) {
  return {
    schema: 'materials2textbook.ask_book.v1',
    book_id: state.book?.book_id || '',
    book_title: state.book?.title || '',
    question,
    sources: results.map((item) => ({
      block_id: item.row.id,
      project_title: item.row.projectTitle,
      task_title: item.row.taskTitle,
      block_title: item.row.blockTitle,
      text: item.row.text,
      evidence_chunk_ids: item.row.evidence || [],
      score: item.score,
    })),
  };
}

function renderRemoteAnswer(payload, answer) {
  answer.innerHTML = '';
  const wrap = el('div', 'ask-result');
  const markdown = el('div', 'markdown');
  markdown.innerHTML = renderMarkdown(sanitizeStudentAnswer(payload.answer || payload.markdown || 'Remote service returned an empty answer.'));
  wrap.appendChild(markdown);
  answer.appendChild(wrap);
}

function sanitizeStudentAnswer(value) {
  return String(value || '')
    .replace(/证据\s*[：:]\s*`?[A-Za-z]{1,5}[_-]?\d{3,}[A-Za-z0-9_-]*`?/gi, '')
    .replace(/chunk_id\s*[：:]\s*`?[A-Za-z]{1,5}[_-]?\d{3,}[A-Za-z0-9_-]*`?/gi, '')
    .replace(/`?[A-Za-z]{1,5}[_-]?\d{3,}[A-Za-z0-9_-]*`?/g, '')
    .replace(/\.(?:mp4|flv|mp3|wav|pptx|ppt|jsonl|docx?)\b/gi, '')
    .replace(/证据编号|来源：|Pending_|待人工|人工复核|时间码|PPT_/g, '')
    .replace(/\s+/g, ' ')
    .trim() || '已找到相关教材内容，请结合对应知识点继续阅读。';
}

function renderAskResult(row, terms) {
  const wrap = el('div', 'ask-result');
  const title = document.createElement('strong');
  const link = document.createElement('a');
  link.href = `#${row.id}`;
  link.textContent = `${row.taskTitle} / ${row.blockTitle}`;
  link.addEventListener('click', () => setActiveSection(row.id));
  title.appendChild(link);
  wrap.appendChild(title);
  wrap.appendChild(paragraph(excerptForTerms(row.text, terms)));
  return wrap;
}

function tokenizeQuestion(value) {
  const terms = [];
  const pushTerm = (term) => {
    const cleaned = String(term || '').toLowerCase().trim();
    if (cleaned.length >= 2) terms.push(cleaned);
  };
  for (const token of String(value || '').split(/[^\p{L}\p{N}_-]+/u)) {
    pushTerm(token);
    const cjkRuns = token.match(/[\u3400-\u9fff]{2,}/g) || [];
    for (const run of cjkRuns) {
      for (const size of [2, 3, 4]) {
        for (let index = 0; index <= run.length - size; index += 1) {
          pushTerm(run.slice(index, index + size));
        }
      }
    }
  }
  return [...new Set(terms)];
}

function scoreAskRow(row, terms) {
  const text = row.text.toLowerCase();
  const title = `${row.taskTitle} ${row.blockTitle}`.toLowerCase();
  let score = 0;
  for (const term of terms) {
    if (text.includes(term)) score += term.length >= 5 ? 3 : 1;
    if (title.includes(term)) score += 4;
  }
  if (['learning_nav', 'assessment', 'exercises'].includes(row.blockType)) score -= 3;
  return score;
}

function preferAskResults(results, terms) {
  const primary = results.filter((item) => !['learning_nav', 'assessment', 'exercises'].includes(item.row.blockType));
  const candidates = primary.length ? primary : results;
  const focus = focusAskTerms(terms);
  if (!focus.length) return candidates;
  const focused = candidates.filter((item) => {
    const haystack = `${item.row.blockTitle} ${item.row.text}`.toLowerCase();
    return focus.some((term) => haystack.includes(term));
  });
  return focused.length ? focused : candidates;
}

function focusAskTerms(terms) {
  const generic = new Set(['操作', '注意', '什么', '怎么', '如何', '要点', '说明', '相关', '学习', '知识', '任务', '操作要', '作要', '要注', '注意什', '意什', '要注意', '注意什么']);
  return terms.filter((term) => term.length >= 2 && !generic.has(term));
}

function excerptForTerms(text, terms) {
  const normalized = text.replace(/\s+/g, ' ').trim();
  const lower = normalized.toLowerCase();
  const index = terms.map((term) => lower.indexOf(term)).find((position) => position >= 0) ?? 0;
  const start = Math.max(0, index - 45);
  const excerpt = normalized.slice(start, start + 150);
  return `${start > 0 ? '...' : ''}${excerpt}${start + 150 < normalized.length ? '...' : ''}`;
}

function listBlock(title, items) {
  const block = el('div', 'block');
  block.appendChild(heading('h4', title));
  block.appendChild(list(items));
  return block;
}

function tagRow(label, items) {
  const wrap = el('div', 'tag-row');
  if (items.length) {
    const strong = document.createElement('strong');
    strong.textContent = `${label}：`;
    wrap.appendChild(strong);
  }
  for (const item of items) {
    const tag = el('span', 'tag');
    tag.textContent = item;
    wrap.appendChild(tag);
  }
  return wrap;
}

function list(items) {
  const ul = document.createElement('ul');
  for (const item of items) {
    const li = document.createElement('li');
    li.textContent = item;
    ul.appendChild(li);
  }
  return ul;
}

function paragraph(text) {
  const p = document.createElement('p');
  p.textContent = text || '';
  return p;
}

function heading(level, text) {
  const h = document.createElement(level);
  h.textContent = text;
  return h;
}

function el(tag, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}

function restoreReaderState() {
  const note = document.getElementById('readerNote');
  note.value = localStorage.getItem(`${state.storageKey}.note`) || '';
  renderBookmarks();
  const progress = Number(localStorage.getItem(`${state.storageKey}.progress`) || '0');
  updateProgress(progress);
  const lastSection = localStorage.getItem(`${state.storageKey}.activeId`);
  if (lastSection && document.getElementById(lastSection) && !location.hash) {
    document.getElementById(lastSection).scrollIntoView({ block: 'start' });
    setActiveSection(lastSection);
  }
}

function bindScrollTracking() {
  const sections = [...document.querySelectorAll('.project, .task, .section-anchor')];
  let ticking = false;
  window.addEventListener('scroll', () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => {
      ticking = false;
      const maxScroll = document.documentElement.scrollHeight - window.innerHeight;
      const progress = maxScroll > 0 ? Math.round((window.scrollY / maxScroll) * 100) : 100;
      updateProgress(progress);
      localStorage.setItem(`${state.storageKey}.progress`, String(progress));
      const current = sections.findLast((section) => section.getBoundingClientRect().top <= 120) || sections[0];
      if (current) setActiveSection(current.id);
    });
  }, { passive: true });
}

function updateProgress(progress) {
  const normalized = Math.max(0, Math.min(100, progress));
  document.getElementById('progressLabel').textContent = `${normalized}%`;
  document.getElementById('progressBar').style.width = `${normalized}%`;
}

function setActiveSection(id) {
  state.activeId = id;
  localStorage.setItem(`${state.storageKey}.activeId`, id);
  for (const link of document.querySelectorAll('.toc a')) {
    link.classList.toggle('active', link.getAttribute('href') === `#${id}`);
  }
  for (const button of document.querySelectorAll('.toc-chapter-toggle')) {
    button.classList.toggle('active', button.dataset.target === id);
  }
}

function getBookmarks() {
  try {
    return JSON.parse(localStorage.getItem(`${state.storageKey}.bookmarks`) || '[]');
  } catch (_error) {
    return [];
  }
}

function saveBookmarks(bookmarks) {
  localStorage.setItem(`${state.storageKey}.bookmarks`, JSON.stringify(bookmarks));
}

function renderBookmarks() {
  const root = document.getElementById('bookmarks');
  root.innerHTML = '';
  for (const bookmark of getBookmarks()) {
    const item = el('div', 'bookmark-item');
    const link = document.createElement('a');
    link.href = `#${bookmark.id}`;
    link.textContent = bookmark.text;
    link.addEventListener('click', () => setActiveSection(bookmark.id));
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = '×';
    remove.title = '删除书签';
    remove.addEventListener('click', () => {
      saveBookmarks(getBookmarks().filter((entry) => entry.id !== bookmark.id));
      renderBookmarks();
    });
    item.appendChild(link);
    item.appendChild(remove);
    root.appendChild(item);
  }
}

function addBookmark() {
  const id = state.activeId || location.hash.replace('#', '') || document.querySelector('.task, .project')?.id;
  if (!id) return;
  const target = document.getElementById(id);
  const heading = target?.querySelector('h2, h3')?.textContent || id;
  const bookmarks = getBookmarks().filter((entry) => entry.id !== id);
  bookmarks.unshift({ id, text: heading });
  saveBookmarks(bookmarks.slice(0, 12));
  renderBookmarks();
}

function collectStudyData() {
  return {
    schema: 'materials2textbook.study_data.v1',
    exported_at: new Date().toISOString(),
    book_id: state.book?.book_id || '',
    book_title: state.book?.title || '',
    progress: Number(localStorage.getItem(`${state.storageKey}.progress`) || '0'),
    active_id: localStorage.getItem(`${state.storageKey}.activeId`) || state.activeId || '',
    font_size: state.fontSize,
    note: localStorage.getItem(`${state.storageKey}.note`) || '',
    bookmarks: getBookmarks(),
  };
}

function exportStudyData() {
  const payload = collectStudyData();
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const link = document.createElement('a');
  const date = new Date().toISOString().slice(0, 10).replaceAll('-', '');
  link.href = URL.createObjectURL(blob);
  link.download = `${payload.book_id || 'digital-book'}-study-data-${date}.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
  showSyncStatus('学习数据已导出。');
}

async function syncStudyDataToEndpoint() {
  const endpoint = (window.DIGITAL_BOOK_STUDY_ENDPOINT || '').trim();
  if (!endpoint) {
    showSyncStatus('No learning-data endpoint is configured. Export a JSON data package instead.');
    return;
  }
  try {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(collectStudyData()),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    showSyncStatus('Learning data synced.');
  } catch (error) {
    showSyncStatus(`Learning data sync failed: ${error.message || error}`);
  }
}

function importStudyDataFile(file) {
  if (!file) return;
  const reader = new FileReader();
  reader.addEventListener('load', () => {
    try {
      const payload = JSON.parse(String(reader.result || '{}'));
      applyStudyData(payload);
      showSyncStatus('学习数据已导入。');
    } catch (_error) {
      showSyncStatus('导入失败：文件不是有效的学习数据 JSON。');
    }
  });
  reader.readAsText(file, 'utf-8');
}

function applyStudyData(payload) {
  if (!payload || payload.schema !== 'materials2textbook.study_data.v1') {
    throw new Error('Unsupported study data schema');
  }
  const currentBookId = state.book?.book_id || '';
  if (payload.book_id && currentBookId && payload.book_id !== currentBookId) {
    throw new Error('Study data belongs to another book');
  }
  const progress = Math.max(0, Math.min(100, Number(payload.progress || 0)));
  localStorage.setItem(`${state.storageKey}.progress`, String(progress));
  if (typeof payload.active_id === 'string') {
    localStorage.setItem(`${state.storageKey}.activeId`, payload.active_id);
  }
  if (typeof payload.note === 'string') {
    localStorage.setItem(`${state.storageKey}.note`, payload.note);
  }
  if (Array.isArray(payload.bookmarks)) {
    saveBookmarks(payload.bookmarks.filter((entry) => entry && entry.id && entry.text).slice(0, 12));
  }
  if (payload.font_size) {
    state.fontSize = Math.max(14, Math.min(24, Number(payload.font_size)));
    localStorage.setItem('reader.fontSize', String(state.fontSize));
    document.documentElement.style.setProperty('--reader-font-size', `${state.fontSize}px`);
  }
  restoreReaderState();
}

function showSyncStatus(message) {
  document.getElementById('syncStatus').textContent = message;
}

function timeToSeconds(value) {
  const parts = value.split(':').map(Number);
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  return Number(value) || 0;
}

document.getElementById('fontSmaller').addEventListener('click', () => {
  state.fontSize = Math.max(14, state.fontSize - 1);
  document.documentElement.style.setProperty('--reader-font-size', `${state.fontSize}px`);
  localStorage.setItem('reader.fontSize', String(state.fontSize));
});

document.getElementById('fontLarger').addEventListener('click', () => {
  state.fontSize = Math.min(24, state.fontSize + 1);
  document.documentElement.style.setProperty('--reader-font-size', `${state.fontSize}px`);
  localStorage.setItem('reader.fontSize', String(state.fontSize));
});

document.getElementById('searchInput').addEventListener('input', (event) => {
  const term = event.target.value.trim().toLowerCase();
  for (const link of document.querySelectorAll('.toc a')) {
    link.style.display = !term || link.textContent.toLowerCase().includes(term) ? '' : 'none';
  }
});

document.getElementById('bookmarkCurrent').addEventListener('click', addBookmark);
document.getElementById('readerNote').addEventListener('input', (event) => {
  localStorage.setItem(`${state.storageKey}.note`, event.target.value);
});
document.getElementById('exportStudyData').addEventListener('click', exportStudyData);
document.getElementById('syncStudyData').addEventListener('click', syncStudyDataToEndpoint);
document.getElementById('importStudyData').addEventListener('click', () => {
  document.getElementById('studyDataFile').click();
});
document.getElementById('studyDataFile').addEventListener('change', (event) => {
  importStudyDataFile(event.target.files?.[0]);
  event.target.value = '';
});
document.getElementById('askButton').addEventListener('click', () => {
  answerQuestion(document.getElementById('askInput').value);
});
document.getElementById('askInput').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') answerQuestion(event.target.value);
});

window.studyDataApi = {
  collect: collectStudyData,
  apply: applyStudyData,
  sync: syncStudyDataToEndpoint,
};

loadBook().catch((error) => {
  document.getElementById('content').textContent = `电子教材加载失败：${error}`;
});
