'use strict';
import {pngSeed} from './png-seed.mjs';
import {posePresets, poseAnalysisPrompt, poseEditPrompt} from './pose-presets.mjs';
const $ = id => document.getElementById(id);
let mode = 'generate', busy = false, inputFile = null, inputUrl = null, currentImage = null;
let serviceBusy = false, healthy = false, startTime = 0, textFormat = 'text';
let inputDimensions = null, inputLoading = false, autoSizePending = false;
let secondFile = null, secondUrl = null, secondLoading = false;
const compositionSteps = [];
let lastAdoptedImage = null;
const history = [], prompts = {generate: '', edit: '', compose: '', analyze: 'この画像を日本語で詳しく説明してください。'};
const labels = {generate: '画像を生成', edit: '画像を編集', compose: '画像を合成', analyze: '画像を解析'};
const placeholders = {generate: '例：窓辺の木製テーブルに赤いティーポットと白いカップ。柔らかな朝の光、写真風。', edit: '例：ティーポットを青色に変更してください。カップと背景はそのまま維持してください。', compose: '例：画像1の人物を画像2の風景の中に配置してください。人物の顔と服装を維持し、光や影を背景に合わせてください。', analyze: '画像について知りたいことを入力してください。'};
const presetState = {edit:{key:'free',target:'',detail:''},compose:{key:'free',target:'',detail:''}};
const keepSingle = '変更範囲を指定した対象に限定してください。それ以外の被写体、背景、構図、画角、位置、形、照明、画風は元の画像のまま維持してください。';
const keepFirst = '画像1を編集の基準としてください。画像2は指定した部分の参照としてのみ使い、画像全体を混ぜたり、画像2の不要な物体を追加したりしないでください。指定箇所以外の画像1の被写体、背景、構図、位置、形、画風は維持してください。';
const presetDefinitions = {
  edit:[
    {key:'color',label:'色だけ変更',target:['変更する物体','例：ティーポット'],detail:['変更後の色','例：青色'],build:(target,detail)=>`画像内の${target}の色だけを${detail}に変更してください。形、質感、陰影は維持してください。${keepSingle}`},
    {key:'remove',label:'物体を取り除く',target:['取り除く物体','例：テーブル上のカップ'],build:target=>`画像内の${target}だけを取り除き、空いた部分を周囲につながる自然な背景で補ってください。他の物体や構図は変更しないでください。`},
    {key:'background',label:'背景だけ変更',detail:['新しい背景','例：夕暮れの海辺'],build:(_,detail)=>`主な被写体の姿、顔、服装、形、ポーズ、位置を維持したまま、背景だけを${detail}に変更してください。被写体を描き替えず、背景との境界を自然になじませてください。`},
    {key:'style',label:'画風だけ変更',detail:['変更後の画風','例：水彩画'],build:(_,detail)=>`この画像を${detail}の画風で描き直してください。被写体の種類、個数、色、形、配置、構図は維持し、新しい物体を追加しないでください。`},
    {key:'text',label:'文字だけ変更',target:['文字のある場所','例：看板の見出し'],detail:['新しい文字','例：ようこそ'],build:(target,detail)=>`${target}に書かれている文字だけを「${detail}」に置き換えてください。文字の周囲、看板の形、他の物体、構図を維持し、新しい文字を読みやすく自然に配置してください。`},
    {key:'text-add',label:'文字を追加',target:['文字を追加する場所','例：画像の上部中央、白い太字'],detail:['追加する文字','例：ようこそ'],build:(target,detail)=>`${target}に「${detail}」という文字を追加してください。指定した文字列を省略・翻訳・言い換えせず、正確に読みやすく配置してください。既存の文字は置き換えず、追加する文字以外の被写体、顔、服装、背景、構図、画風は元の画像のまま維持してください。`},
    {key:'pose',label:'ポーズを変更',target:['ポーズを変える対象','例：中央の人物'],detail:['変更後のポーズ','例：右手を上げて挨拶する'],build:(target,detail)=>`画像内の${target}のポーズを「${detail}」に変更してください。同じ被写体として顔立ち、表情、髪型、服装、体形、色、画風を維持してください。手足や関節、体の向き、重心を自然に整え、姿勢の変更に必要な部分だけを描き替えてください。他の被写体や背景、画角、構図はできる限り元の画像のまま維持してください。`}
  ],
  compose:[
    {key:'clothes',label:'服だけ置き換える',build:()=>`画像1の人物が着ている服だけを、画像2の服のデザイン、色、素材に変更してください。画像1の人物の顔、髪、体形、ポーズ、手足、背景は維持してください。服を体の形とポーズに自然に合わせてください。${keepFirst}`},
    ...posePresets.map(preset=>({...preset,build:()=>preset.prompt})),
    {key:'color',label:'色だけ合わせる',target:['色を変える物体（画像1）','例：ティーポット'],build:target=>`画像1の${target}の色だけを、画像2の対応する物体の色に合わせてください。画像1の対象の形、材質、陰影、位置、大きさを維持してください。${keepFirst}`},
    {key:'add',label:'物体だけ追加する',target:['追加する物体（画像2）','例：花瓶'],detail:['配置する場所（画像1）','例：テーブルの右側'],build:(target,detail)=>`画像2の${target}だけを画像1の${detail}に追加してください。画像2の他の物体や背景は追加しないでください。追加する物体の形と色を保ち、画像1の光、影、遠近感、大きさに自然に合わせてください。画像1の既存の物体と背景、構図は維持してください。`},
    {key:'background',label:'背景だけ置き換える',build:()=>`画像1の主な被写体をそのまま維持し、背景だけを画像2の背景に置き換えてください。画像1の被写体の顔、服装、形、ポーズを変更しないでください。画像2の人物や不要な物体を追加せず、光、影、遠近感を自然に合わせてください。`},
    {key:'texture',label:'素材・模様だけ合わせる',target:['変更する部分（画像1）','例：ソファの表面'],build:target=>`画像1の${target}の素材や表面の模様だけを、画像2の対応する部分を参考に変更してください。画像1の対象の形、輪郭、位置、大きさを維持し、模様を表面の曲面と遠近に自然に沿わせてください。${keepFirst}`},
    {key:'style',label:'画風だけ合わせる',build:()=>`画像1の内容を、画像2の線、筆致、質感、色調の画風で描き直してください。画像1の被写体の種類、個数、構図、位置、形を維持してください。画像2から物体や人物を持ち込まないでください。`}
  ]
};
function savePresetState() {
  if (!presetState[mode]) return;
  presetState[mode] = {key:$('preset').value,target:$('preset-target').value,detail:$('preset-detail').value};
}
function selectedPreset() { return (presetDefinitions[mode] || []).find(item=>item.key === $('preset').value); }
function renderPresetFields() {
  const definition = selectedPreset();
  for (const field of ['target','detail']) {
    const settings = definition?.[field];
    $('preset-'+field+'-area').hidden = !settings;
    $('preset-'+field).required = !!settings;
    if (settings) { $('preset-'+field+'-label').textContent = settings[0]; $('preset-'+field).placeholder = settings[1]; }
  }
  $('prompt-label').textContent = definition ? '指示文（変更可）' : {generate:'作りたい画像',edit:'変更したいこと',compose:'合成したい内容',analyze:'画像への質問'}[mode];
}
function configurePresets() {
  const state = presetState[mode];
  $('preset-area').hidden = !state;
  $('preset').replaceChildren();
  if (state) {
    const free = new Option('自由入力','free'); $('preset').add(free);
    presetDefinitions[mode].forEach(item=>$('preset').add(new Option(item.label,item.key)));
    $('preset').value = state.key;
    $('preset-target').value = state.target; $('preset-detail').value = state.detail;
  }
  renderPresetFields();
}
function applyPreset() {
  const definition = selectedPreset(); if (!definition) return;
  $('prompt').value = definition.build($('preset-target').value.trim() || '［対象］',$('preset-detail').value.trim() || '［変更内容］');
  prompts[mode] = $('prompt').value; savePresetState();
}

function updateButton() {
  const loading = (mode !== 'generate' && inputLoading) || (mode === 'compose' && secondLoading);
  $('submit').disabled = busy || serviceBusy || !healthy || loading;
  $('submit').textContent = busy ? '処理しています…' : loading ? '画像を読み込み中…' : !healthy ? 'APIへの接続待ち' : serviceBusy ? '他の処理が実行中です' : labels[mode] + ' ↗';
  document.querySelectorAll('.tab, #use-edit, #use-analyze, #clear-input, #file, #clear-input2, #file2, #preset, #preset-target, #preset-detail, #seed, #random-seed, #reuse-seed, #prompt, #size').forEach(el => el.disabled = busy);
  $('use-compose').hidden = mode !== 'compose' || currentImage?.kind !== 'compose';
  $('use-compose').disabled = busy || inputLoading || currentImage === lastAdoptedImage;
  $('undo-compose').hidden = mode !== 'compose' || !compositionSteps.length;
  $('undo-compose').disabled = busy || inputLoading;
}
function switchMode(next) {
  if (busy) return;
  prompts[mode] = $('prompt').value;
  savePresetState();
  mode = next;
  $('prompt').value = prompts[mode];
  $('prompt').placeholder = placeholders[mode];
  $('prompt-label').textContent = {generate:'作りたい画像',edit:'変更したいこと',compose:'合成したい内容',analyze:'画像への質問'}[mode];
  $('upload-area').hidden = mode === 'generate';
  $('image-options').hidden = mode === 'analyze';
  $('second-upload').hidden = mode !== 'compose';
  $('upload-area').classList.toggle('two-images',mode === 'compose');
  $('input-label').textContent = mode === 'compose' ? '画像1' : '入力画像';
  $('open-input-preview').setAttribute('aria-label', mode === 'compose' ? '画像1を拡大' : '入力画像を拡大');
  if (mode === 'edit' || mode === 'compose') $('upload-area').after($('image-options'));
  else $('prompt').after($('image-options'));
  selectEditSize();
  $('analysis-options').hidden = mode !== 'analyze';
  configurePresets();
  document.querySelectorAll('.tab').forEach(el => {
    const active = el.dataset.mode === mode;
    el.classList.toggle('active', active); el.setAttribute('aria-pressed', String(active));
  });
  $('error').hidden = true; updateButton();
}
function fail(message) { $('error').textContent = message; $('error').hidden = false; }
function selectEditSize() {
  if (!['edit','compose'].includes(mode) || !inputDimensions || !autoSizePending) return;
  const {width,height} = inputDimensions;
  const sizes = [...$('size').options].map(option => {
    const [w,h] = option.value.split('x').map(Number);
    return {value:option.value, ratio:Math.abs(Math.log((w/h)/(width/height))), area:Math.abs(Math.log((w*h)/(width*height)))};
  });
  sizes.sort((a,b) => Math.abs(a.ratio-b.ratio) > 1e-9 ? a.ratio-b.ratio : a.area-b.area);
  $('size').value = sizes[0].value;
  autoSizePending = false;
}
function setInput(file, resetSteps = true) {
  if (resetSteps) { compositionSteps.length = 0; lastAdoptedImage = null; }
  if (inputUrl) URL.revokeObjectURL(inputUrl);
  inputFile = file; inputUrl = file ? URL.createObjectURL(file) : null;
  inputDimensions = null; inputLoading = !!file; autoSizePending = !!file;
  $('input-preview').hidden = !file; $('open-input-preview').hidden = !file; $('upload-label').hidden = !!file; $('clear-input').hidden = !file;
  if (file) $('input-preview').src = inputUrl; else $('input-preview').removeAttribute('src');
  updateButton();
  if (file) {
    const decoded = new Image();
    decoded.onload = () => {
      if (inputFile !== file) return;
      inputDimensions = {width:decoded.naturalWidth,height:decoded.naturalHeight};
      inputLoading = false; selectEditSize(); updateButton();
    };
    decoded.onerror = () => {
      if (inputFile !== file) return;
      setInput(null); $('file').value = ''; fail('画像を読み込めませんでした。PNG・JPEG・静止WebPを選び直してください。');
    };
    decoded.src = inputUrl;
  }
}
function asDataUrl(file) {
  return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(new Error('画像を読み込めませんでした')); reader.readAsDataURL(file); });
}
function setSecondInput(file) {
  if (secondUrl) URL.revokeObjectURL(secondUrl);
  secondFile = file; secondUrl = file ? URL.createObjectURL(file) : null; secondLoading = !!file;
  $('input-preview2').hidden = !file; $('open-input-preview2').hidden = !file; $('upload-label2').hidden = !!file; $('clear-input2').hidden = !file;
  if (file) {
    const preview = $('input-preview2');
    preview.onload = () => { if (secondFile === file) { secondLoading = false; updateButton(); } };
    preview.onerror = () => { if (secondFile === file) { setSecondInput(null); $('file2').value = ''; fail('画像2を読み込めませんでした。PNG・JPEG・静止WebPを選び直してください。'); } };
    preview.src = secondUrl;
  } else { $('input-preview2').removeAttribute('src'); }
  updateButton();
}
function openImagePreview(url, label) {
  if (!url) return;
  $('input-preview-title').textContent = label;
  $('expanded-input-preview').src = url;
  $('expanded-input-preview').alt = label + 'の拡大表示';
  $('input-preview-dialog').showModal();
}
$('open-input-preview').onclick = () => openImagePreview(inputUrl, mode === 'compose' ? '画像1' : '入力画像');
$('open-input-preview2').onclick = () => openImagePreview(secondUrl, '画像2');
$('open-result-preview').onclick = () => {
  if (busy || !currentImage) return;
  openImagePreview(currentImage.url, {generate:'生成結果',edit:'編集結果',compose:'合成結果'}[currentImage.kind] || '結果画像');
};
$('close-input-preview').onclick = () => $('input-preview-dialog').close();
$('input-preview-dialog').onclick = event => { if (event.target === $('input-preview-dialog')) $('input-preview-dialog').close(); };
$('input-preview-dialog').onclose = () => $('expanded-input-preview').removeAttribute('src');
async function checkResponse(response) {
  if (response.ok) return;
  let message = 'APIエラー (' + response.status + ')';
  try { const body = await response.json(); message = body.error?.message || message; } catch {}
  if (response.status === 429) message = '別の推論が実行中です。完了後にもう一度お試しください。';
  throw new Error(message);
}
function displayImage(item) {
  currentImage = item;
  $('empty').hidden = true; $('text-result').hidden = true; $('image-result').hidden = false;
  $('output-image').src = item.url; $('download').href = item.url; $('download').download = item.name;
  $('result-info').textContent = item.info;
  $('result-title').textContent = {edit:'編集結果',compose:'合成結果',generate:'生成結果'}[item.kind];
  $('seed-result').hidden = item.seed === null;
  $('result-seed').textContent = item.seed === null ? '' : 'シード ' + item.seed;
  updateButton();
}
function rememberImage(blob, info, seed) {
  const item = {blob, url:URL.createObjectURL(blob), name:'image-' + Date.now() + '.png', info, kind:mode, seed};
  history.unshift(item);
  if (history.length > 6) { const old = history.pop(); URL.revokeObjectURL(old.url); }
  $('history-images').replaceChildren(); $('history').hidden = false;
  history.forEach(image => { const button = document.createElement('button'); const img = document.createElement('img'); img.src = image.url; img.alt = image.info; button.title = 'この画像を表示'; button.type = 'button'; button.append(img); button.onclick = () => { if (!busy) displayImage(image); }; $('history-images').append(button); });
  displayImage(item);
}
function prepareText() {
  $('empty').hidden = true; $('image-result').hidden = true; $('text-result').hidden = false;
  $('output-text').textContent = ''; $('result-info').textContent = '解析しています…';
  $('result-title').textContent = '解析結果';
}
async function readStream(response) {
  const reader = response.body.getReader(), decoder = new TextDecoder();
  let buffer = '', done = false;
  function line(value) {
    if (!value.startsWith('data:')) return;
    const data = value.slice(5).trim(); if (!data) return;
    if (data === '[DONE]') { done = true; return; }
    const event = JSON.parse(data);
    if (event.error) throw new Error(event.error.message || '解析中にエラーが発生しました');
    for (const choice of event.choices || []) $('output-text').textContent += choice.delta?.content || '';
  }
  try {
    while (true) {
      const part = await reader.read(); buffer += decoder.decode(part.value || new Uint8Array(), {stream:!part.done});
      const lines = buffer.split('\n'); buffer = lines.pop(); lines.forEach(line);
      if (part.done) break;
    }
    if (buffer) line(buffer);
    if (!done) throw new Error('解析の接続が途中で終了しました。APIの処理完了を待って再試行してください。');
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}
async function refreshHealth() {
  try {
    const response = await fetch('/api/health', {cache:'no-store'}), health = await response.json();
    healthy = response.ok && health.status === 'ok'; serviceBusy = !!health.inference_busy;
  } catch { healthy = false; }
  updateButton();
}
document.querySelectorAll('.tab').forEach(el => el.onclick = () => switchMode(el.dataset.mode));
$('file').onchange = () => {
  $('error').hidden = true; const file = $('file').files[0]; if (!file) return;
  if (!['image/png','image/jpeg','image/webp'].includes(file.type) || file.size > 16 * 1024 * 1024) {
    $('file').value = ''; setInput(null); fail('PNG・JPEG・静止WebPを16MiB以内で指定してください。'); return;
  }
  setInput(file);
};
$('clear-input').onclick = () => { setInput(null); $('file').value = ''; };
$('file2').onchange = () => {
  $('error').hidden = true; const file = $('file2').files[0]; if (!file) return;
  if (!['image/png','image/jpeg','image/webp'].includes(file.type) || file.size > 16 * 1024 * 1024) {
    $('file2').value = ''; setSecondInput(null); fail('画像2はPNG・JPEG・静止WebPを16MiB以内で指定してください。'); return;
  }
  setSecondInput(file);
};
$('clear-input2').onclick = () => { setSecondInput(null); $('file2').value = ''; };
$('format').onchange = () => { $('schema-area').hidden = $('format').value !== 'json'; };
$('size').onchange = () => { autoSizePending = false; };
function updateSeedSetting() { $('advanced-label').textContent = $('seed').value.trim() ? '詳細設定 · シード固定' : '詳細設定'; }
$('seed').oninput = updateSeedSetting;
$('random-seed').onclick = () => { $('seed').value = ''; updateSeedSetting(); $('advanced').open = false; $('progress').textContent = ''; };
$('reuse-seed').onclick = () => {
  if (busy || currentImage?.seed == null) return;
  if (mode === 'analyze') switchMode(currentImage.kind);
  $('seed').value = currentImage.seed; updateSeedSetting(); $('advanced').open = true;
  $('progress').textContent = '次の処理でシード ' + currentImage.seed + ' を使用します';
};
$('preset').onchange = () => {
  $('preset-target').value = ''; $('preset-detail').value = '';
  renderPresetFields(); savePresetState(); applyPreset();
};
for (const id of ['preset-target','preset-detail']) $(id).oninput = applyPreset;
function useImage(next) {
  if (!currentImage || busy) return;
  setInput(new File([currentImage.blob], currentImage.name, {type:'image/png'})); $('file').value = '';
  if (next === 'edit' && !prompts.edit) prompts.edit = 'ティーポットを青色に変更してください。その他は維持してください。';
  switchMode(next); $('prompt').focus(); window.scrollTo({top:0,behavior:'smooth'});
}
$('use-edit').onclick = () => useImage('edit'); $('use-analyze').onclick = () => useImage('analyze');
$('use-compose').onclick = () => {
  if (busy || inputLoading || !currentImage || mode !== 'compose' || currentImage === lastAdoptedImage) return;
  compositionSteps.push({file:inputFile,size:$('size').value,adopted:lastAdoptedImage});
  if (compositionSteps.length > 10) compositionSteps.shift();
  lastAdoptedImage = currentImage;
  const size = $('size').value;
  setInput(new File([currentImage.blob],currentImage.name,{type:'image/png'}),false);
  autoSizePending = false; $('size').value = size; $('file').value = '';
  $('progress').textContent = '画像1に採用しました。次のパーツを選択してください。';
  $('preset').focus(); updateButton();
};
$('undo-compose').onclick = () => {
  if (busy || inputLoading || !compositionSteps.length) return;
  const step = compositionSteps.pop(); lastAdoptedImage = step.adopted;
  setInput(step.file,false); autoSizePending = false; $('size').value = step.size; $('file').value = '';
  currentImage = null; $('image-result').hidden = true; $('text-result').hidden = true; $('empty').hidden = false;
  $('result-title').textContent = 'プレビュー'; $('result-info').textContent = '';
  $('progress').textContent = '画像1を採用前に戻しました。結果は履歴から再表示できます。';
  updateButton();
};
$('save-text').onclick = () => {
  const url = URL.createObjectURL(new Blob([$('output-text').textContent], {type:textFormat === 'json' ? 'application/json' : 'text/plain;charset=utf-8'}));
  const a = document.createElement('a'); a.href = url; a.download = 'image-analysis.' + (textFormat === 'json' ? 'json' : 'txt'); a.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
};
$('form').onsubmit = async event => {
  event.preventDefault(); if (busy || serviceBusy || (mode !== 'generate' && inputLoading) || (mode === 'compose' && secondLoading)) return;
  $('error').hidden = true;
  if (mode !== 'generate' && !inputFile) { fail('入力画像を選択してください。'); return; }
  if (mode === 'compose' && !secondFile) { fail('画像2を選択してください。'); return; }
  if (mode === 'compose' && inputFile.size + secondFile.size > 32 * 1024 * 1024 - 64 * 1024) { fail('2枚の画像の合計容量を32MiB未満にしてください。'); return; }
  const prompt = $('prompt').value.trim(); if (!prompt) { fail('指示を入力してください。'); return; }
  let seed, schema;
  if (mode !== 'analyze' && $('seed').value.trim()) {
    seed = Number($('seed').value.trim());
    if (!Number.isSafeInteger(seed) || seed < -1) { fail('シードは-1から9007199254740991までの整数を指定してください。'); return; }
  }
  if (mode === 'analyze' && $('format').value === 'json' && $('schema').value.trim()) {
    try { schema = JSON.parse($('schema').value); if (!schema || typeof schema !== 'object' || Array.isArray(schema)) throw new Error(); }
    catch { fail('JSON Schemaを正しいJSONオブジェクトで入力してください。'); return; }
  }
  busy = true; startTime = Date.now(); updateButton();
  const previousVisibility = ['empty','image-result','text-result'].map(id => [id,$(id).hidden]);
  let completed = false;
  if (mode !== 'analyze') {
    previousVisibility.forEach(([id]) => { $(id).hidden = true; });
    $('processing').hidden = false;
    $('result-title').textContent = {edit:'編集中',compose:'合成中',generate:'生成中'}[mode];
    $('processing-title').textContent = {edit:'画像を編集しています…',compose:'画像を合成しています…',generate:'画像を生成しています…'}[mode];
    $('processing-elapsed').textContent = '0秒';
    $('result-info').textContent = '';
  }
  const timer = setInterval(() => {
    const seconds = Math.floor((Date.now()-startTime)/1000);
    $('progress').textContent = '処理中 · ' + seconds + '秒（推論完了を待っています）';
    $('processing-elapsed').textContent = seconds + '秒';
  },1000);
  $('progress').textContent = 'APIへ送信しています…';
  try {
    if (mode === 'analyze') {
      const image = await asDataUrl(inputFile); textFormat = $('format').value;
      let response;
      if (textFormat === 'text') {
        response = await fetch('/api/v1/chat/completions', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model:'qwen-image-analysis',messages:[{role:'user',content:[{type:'text',text:prompt},{type:'image_url',image_url:{url:image}}]}],max_tokens:1024,stream:true})});
        await checkResponse(response); prepareText(); await readStream(response);
      } else {
        response = await fetch('/api/v1/images/analyze', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image,prompt,format:'json',...(schema ? {schema} : {})})});
        await checkResponse(response); const result = await response.json(); prepareText(); $('output-text').textContent = JSON.stringify(result.result,null,2);
      }
      $('result-info').textContent = '解析完了 · ' + ((Date.now()-startTime)/1000).toFixed(1) + '秒';
    } else {
      let response; const size = $('size').value;
      if (mode === 'generate') response = await fetch('/api/v1/images/generations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model:'qwen-image-2.1-turbo',prompt,size,n:1,...(seed !== undefined ? {seed} : {})})});
      else {
        let imagePrompt = prompt;
        const posePreset = mode === 'compose' && posePresets.find(item=>item.key === $('preset').value);
        const poseOnly = !!posePreset;
        if (poseOnly) {
          $('processing-title').textContent = '画像2のポーズを確認しています…';
          const poseResponse = await fetch('/api/v1/images/analyze', {
            method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
              image:await asDataUrl(secondFile),format:'text',
              prompt:poseAnalysisPrompt(posePreset)
            })
          });
          await checkResponse(poseResponse);
          const pose = (await poseResponse.json()).result;
          if (typeof pose !== 'string' || !pose.trim()) throw new Error('画像2のポーズを読み取れませんでした。自由入力で姿勢を補ってお試しください。');
          // Avoid repeating the generic preset: the editor receives only image1, so
          // references to an absent "image2" obscure the concrete pose instruction.
          imagePrompt = poseEditPrompt(posePreset,pose,prompt);
          if (imagePrompt.length > 8000) throw new Error('指示文が長すぎます。ポーズの説明を追加できるよう短くしてください。');
          $('processing-title').textContent = '画像を合成しています…';
        }
        const form = new FormData();
        // Pose references supply text only; passing image2 also transfers its appearance.
        const useComposition = mode === 'compose' && !poseOnly;
        if (useComposition) { form.set('image1',inputFile); form.set('image2',secondFile); }
        else form.set('image',inputFile);
        form.set('prompt',imagePrompt); form.set('model','qwen-image-2.1-turbo'); form.set('size',size); form.set('n','1');
        if (seed !== undefined) form.set('seed',String(seed));
        response = await fetch(useComposition ? '/api/v1/images/compositions' : '/api/v1/images/edits',{method:'POST',body:form});
      }
      await checkResponse(response); const result = await response.json();
      const bytes = Uint8Array.from(atob(result.data[0].b64_json), c => c.charCodeAt(0));
      rememberImage(new Blob([bytes],{type:'image/png'}), size.replace('x',' × ') + ' · ' + ((Date.now()-startTime)/1000).toFixed(1) + '秒',pngSeed(bytes));
    }
    $('progress').textContent = '完了しました';
    completed = true;
  } catch(error) { fail(error.message || '処理に失敗しました'); $('progress').textContent = ''; }
  finally {
    clearInterval(timer);
    if (mode !== 'analyze') {
      $('processing').hidden = true;
      $('input-preview').hidden = !inputFile;
      if (!completed) {
        previousVisibility.forEach(([id,hidden]) => { $(id).hidden = hidden; });
        $('result-title').textContent = {edit:'編集できませんでした',compose:'合成できませんでした',generate:'生成できませんでした'}[mode];
        $('result-info').textContent = '表示は処理前の結果です';
      }
    }
    busy = false; await refreshHealth();
  }
};
refreshHealth(); setInterval(refreshHealth,5000);
