#!/usr/bin/env node
/**
 * build_meeting_note.js — CIP 하우스 양식 미팅노트(.docx) 생성기
 *
 * 사용법:
 *   node build_meeting_note.js <input.json> <output.docx>
 *
 * 입력 JSON 스키마는 skills/meeting-summary/references/format_spec.md 참조.
 * 요약:
 * {
 *   "title": "Project Union Meeting Note",
 *   "subject": "캐피탈(산은캐피탈) 출신 전문가 인터뷰",
 *   "date": "2026-07-24 (금) 17:00~18:00",
 *   "attendee": "김진만 상무, 조계승 이사",
 *   "interviewee": "산은캐피탈 출신 전문가",          // 선택 (인터뷰인 경우)
 *   "executiveSummary": [                             // 소제목 필수 — 자동 번호
 *     { "heading": "캐피탈 수입 구조", "bullets": [ <item>... ] }
 *   ],
 *   "sections": [                                     // 자동 번호 1., 1-1.
 *     { "heading": "제목", "content": [ <item>... ],
 *       "subsections": [ { "heading": "소제목", "content": [ <item>... ] } ] }
 *   ]
 * }
 *
 * <item> = "문자열"                                    → "- " 불릿
 *        | { "text": "...", "level": 2 }              → "· " 하위 불릿(들여쓰기)
 *        | { "text": "...", "emphasis": true }        → 굵게+밑줄 (핵심 강조)
 *        | { "text": "...", "footnote": "각주 내용" }  → 문장 끝 각주 마커
 *        | { "heading": "이름" }                       → 번호 없는 굵은 소제목 행
 *        | { "table": { "caption", "columns", "rows", "source", "note", "widths" } }
 */
'use strict';

const fs = require('fs');
const path = require('path');

let docx;
try {
  docx = require('docx');
} catch (e) {
  try {
    docx = require(path.join(process.cwd(), 'node_modules', 'docx'));
  } catch (e2) {
    console.error('docx 패키지를 찾을 수 없습니다. 실행 위치에서 `npm install docx` 후 다시 시도하세요.');
    process.exit(1);
  }
}

const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  AlignmentType, BorderStyle, ShadingType, WidthType, VerticalAlign,
  FootnoteReferenceRun, ImageRun, Footer, PageNumber,
} = docx;

// ---------- 하우스 스타일 상수 (참조 문서에서 추출한 값 — 임의 변경 금지) ----------
const FONT = { ascii: 'Arial', hAnsi: 'Arial', eastAsia: '맑은 고딕' };
const SZ = {
  title: 24,     // 12pt — 문서 제목, "Executive Summary" (Union)
  body: 20,      // 10pt — 본문 (White)
  table: 18,     // 9pt  — 표 본문
  caption: 19,   // 9.5pt — [표 N] 캡션 (CIP)
  source: 15,    // 7.5pt — Source/Note 행 (CIP)
  footnote: 16,  // 8pt  — 각주
};
const COLOR = {
  text: '000000',        // 흑백톤 — 본문은 검정만
  meta: '999999',        // Source/Note 행 회색 (CIP)
  footnote: '595959',
  tableBorder: 'D9D9D9', // 표 테두리 연회색 (CIP)
  headerFill: 'F2F2F2',  // 표 머리행 음영 (CIP)
};
const PAGE_MARGIN = 737;            // 13mm (White/Union 공통)
const CONTENT_WIDTH = 10432;        // A4 210mm - 좌우 13mm = 184mm (DXA)
const LINE = { line: 256, lineRule: 'auto' };  // White 행간

// White 원본과 동일한 문서 기본값: 한글 문서 표준(autoSpace·wordWrap 해제)으로
// 숫자·한글 사이 자동 여백 없이 조밀하게 조판된다. 각주 스타일 포함.
const STYLES_XML = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults>
    <w:rPrDefault><w:rPr>
      <w:rFonts w:ascii="Arial" w:eastAsia="맑은 고딕" w:hAnsi="Arial" w:cs="Arial"/>
      <w:color w:val="000000"/><w:sz w:val="20"/><w:szCs w:val="20"/>
      <w:lang w:val="en-US" w:eastAsia="ko-KR"/>
    </w:rPr></w:rPrDefault>
    <w:pPrDefault><w:pPr><w:spacing w:after="40" w:line="256" w:lineRule="auto"/></w:pPr></w:pPrDefault>
  </w:docDefaults>
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/><w:qFormat/>
    <w:pPr><w:widowControl w:val="0"/><w:wordWrap w:val="0"/>
      <w:autoSpaceDE w:val="0"/><w:autoSpaceDN w:val="0"/></w:pPr>
  </w:style>
  <w:style w:type="character" w:default="1" w:styleId="DefaultParagraphFont">
    <w:name w:val="Default Paragraph Font"/><w:uiPriority w:val="1"/>
  </w:style>
  <w:style w:type="paragraph" w:styleId="FootnoteText">
    <w:name w:val="footnote text"/><w:basedOn w:val="Normal"/><w:uiPriority w:val="99"/>
    <w:pPr><w:spacing w:after="0"/></w:pPr>
    <w:rPr><w:sz w:val="16"/><w:szCs w:val="16"/><w:color w:val="595959"/></w:rPr>
  </w:style>
  <w:style w:type="character" w:styleId="FootnoteReference">
    <w:name w:val="footnote reference"/><w:uiPriority w:val="99"/>
    <w:rPr><w:vertAlign w:val="superscript"/></w:rPr>
  </w:style>
</w:styles>`;

// ---------- 헬퍼 ----------
const state = { footnotes: {}, nextFootnoteId: 1 };

function runs(text, opts = {}) {
  return new TextRun({
    text,
    font: FONT,
    size: opts.size || SZ.body,
    bold: !!opts.bold,
    italics: !!opts.italics,
    underline: opts.underline ? {} : undefined,
    color: opts.color || COLOR.text,
  });
}

function para(children, opts = {}) {
  return new Paragraph({
    children,
    alignment: opts.alignment,
    spacing: { ...LINE, before: opts.before || 0, after: opts.after ?? 40 },
    indent: opts.indent,
    keepNext: opts.keepNext,
    border: opts.border,
  });
}

function headingPara(text, opts = {}) {
  return para([runs(text, { bold: true, size: opts.size || SZ.body })], {
    before: opts.before ?? 160,
    after: opts.after ?? 60,
    keepNext: true,
  });
}

function registerFootnote(text) {
  const id = state.nextFootnoteId++;
  state.footnotes[id] = {
    children: [new Paragraph({
      children: [runs(' ' + text, { size: SZ.footnote, color: COLOR.footnote })],
      spacing: { ...LINE, after: 0 },
    })],
  };
  return id;
}

// 불릿 아이템 → Paragraph. 발화 내용은 양쪽 정렬(Union/CIP), 제목류는 왼쪽.
function bulletPara(item) {
  const obj = typeof item === 'string' ? { text: item } : item;

  if (obj.heading !== undefined) {
    return headingPara(obj.heading, { before: 120, after: 40 });
  }

  const level2 = obj.level === 2;
  const marker = level2 ? '· ' : '- ';
  const style = { bold: !!obj.emphasis, underline: !!obj.emphasis };
  const children = [runs(marker + obj.text, style)];

  const notes = obj.footnotes || (obj.footnote ? [obj.footnote] : []);
  for (const n of notes) {
    children.push(new FootnoteReferenceRun(registerFootnote(n)));
  }

  return para(children, {
    alignment: AlignmentType.JUSTIFIED,
    indent: level2 ? { left: 360 } : undefined,
  });
}

function borders() {
  const b = { style: BorderStyle.SINGLE, size: 4, color: COLOR.tableBorder };
  return { top: b, bottom: b, left: b, right: b, insideHorizontal: b, insideVertical: b };
}

function cellPara(text, opts = {}) {
  return new Paragraph({
    children: [runs(String(text), { size: SZ.table, bold: !!opts.bold })],
    alignment: opts.alignment || AlignmentType.LEFT,
    spacing: { ...LINE, after: 0 },
  });
}

function buildTable(spec, tableIndex) {
  const cols = spec.columns || [];
  const ratios = spec.widths && spec.widths.length === cols.length
    ? spec.widths : cols.map(() => 1);
  const sum = ratios.reduce((a, b) => a + b, 0);
  const colWidths = ratios.map(r => Math.floor(CONTENT_WIDTH * r / sum));
  // 반올림 오차는 마지막 열에 흡수 (열 합계 = 표 너비 필수)
  colWidths[colWidths.length - 1] += CONTENT_WIDTH - colWidths.reduce((a, b) => a + b, 0);

  const headerRow = new TableRow({
    tableHeader: true,
    children: cols.map((c, i) => new TableCell({
      width: { size: colWidths[i], type: WidthType.DXA },
      shading: { type: ShadingType.CLEAR, fill: COLOR.headerFill },
      verticalAlign: VerticalAlign.CENTER,
      children: [cellPara(c, { bold: true, alignment: AlignmentType.CENTER })],
    })),
  });

  const dataRows = (spec.rows || []).map(row => new TableRow({
    children: row.map((cell, i) => new TableCell({
      width: { size: colWidths[i], type: WidthType.DXA },
      verticalAlign: VerticalAlign.CENTER,
      children: [cellPara(cell)],
    })),
  }));

  const out = [];
  if (spec.caption) {
    out.push(para([runs(`[표 ${tableIndex}] ${spec.caption}`, { bold: true, size: SZ.caption })],
      { before: 120, after: 40, keepNext: true }));
  }
  out.push(new Table({
    width: { size: CONTENT_WIDTH, type: WidthType.DXA },
    columnWidths: colWidths,
    borders: borders(),
    margins: { top: 30, bottom: 30, left: 80, right: 80 },
    rows: [headerRow, ...dataRows],
  }));

  const metaBits = [];
  if (spec.note) metaBits.push(`Note: ${spec.note}`);
  if (spec.source) metaBits.push(`Source: ${spec.source}`);
  if (metaBits.length) {
    out.push(para([runs(metaBits.join('  '), { italics: true, size: SZ.source, color: COLOR.meta })],
      { after: 120 }));
  } else {
    out.push(para([], { after: 80 }));
  }
  return out;
}

function contentBlocks(items, counters) {
  const out = [];
  for (const item of items || []) {
    if (item && typeof item === 'object' && item.table) {
      out.push(...buildTable(item.table, counters.table++));
    } else {
      out.push(bulletPara(item));
    }
  }
  return out;
}

// ---------- 문서 조립 ----------
function build(data) {
  const children = [];
  const counters = { table: 1 };

  // CENTROID 로고 (Union/White 공통 — 1페이지 상단, 1.97in x 0.56in)
  if (data.logo !== false) {
    const logoPath = data.logoPath || path.join(__dirname, '..', 'assets', 'centroid_logo.jpg');
    if (fs.existsSync(logoPath)) {
      children.push(new Paragraph({
        children: [new ImageRun({
          type: 'jpg',
          data: fs.readFileSync(logoPath),
          transformation: { width: 189, height: 54 },   // 1800225 x 514350 EMU (원본과 동일)
        })],
        spacing: { ...LINE, after: 200 },
      }));
    }
  }

  // 제목 + 하단 구분선 (Union: single sz6 A0A0A0)
  children.push(para([runs(data.title || 'Meeting Note', { bold: true, size: SZ.title })], {
    after: 120,
    border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: 'A0A0A0', space: 1 } },
  }));

  // 메타 블록 (White/Union 공통 — 전체 굵게)
  if (data.subject) children.push(para([runs(`Subject: ${data.subject}`, { bold: true })]));
  if (data.date) children.push(para([runs(`Date: ${data.date}`, { bold: true })]));
  {
    let att = `Attendee: ${data.attendee || ''}`;
    if (data.interviewee) att += `  |  Interviewee : ${data.interviewee}`;
    children.push(para([runs(att, { bold: true })], { after: 120 }));
  }

  // Executive Summary — 소제목(자동 번호) 필수
  if (data.executiveSummary && data.executiveSummary.length) {
    children.push(headingPara('Executive Summary', { size: SZ.title, before: 120, after: 80 }));
    data.executiveSummary.forEach((topic, i) => {
      children.push(headingPara(`${i + 1}. ${topic.heading}`, { before: 100, after: 40 }));
      children.push(...contentBlocks(topic.bullets, counters));
    });
    children.push(para([], { after: 120 }));
  }

  // 본문 섹션 — 자동 번호 N., N-M.
  (data.sections || []).forEach((sec, i) => {
    children.push(headingPara(`${i + 1}. ${sec.heading}`, { before: 200 }));
    children.push(...contentBlocks(sec.content, counters));
    (sec.subsections || []).forEach((sub, j) => {
      children.push(headingPara(`${i + 1}-${j + 1}. ${sub.heading}`, { before: 140 }));
      children.push(...contentBlocks(sub.content, counters));
    });
  });

  // E.O.D 마커 (Union)
  children.push(para(
    [runs('*'.repeat(55) + 'E.O.D' + '*'.repeat(55), { italics: true })],
    { alignment: AlignmentType.CENTER, before: 240 },
  ));

  return new Document({
    footnotes: state.footnotes,
    sections: [{
      properties: {
        page: {
          margin: { top: PAGE_MARGIN, bottom: PAGE_MARGIN, left: PAGE_MARGIN, right: PAGE_MARGIN },
        },
      },
      footers: {
        // Union 양식: 하단 중앙 "현재쪽/전체쪽"
        default: new Footer({
          children: [new Paragraph({
            alignment: AlignmentType.CENTER,
            spacing: { ...LINE, after: 0 },
            children: [new TextRun({
              children: [PageNumber.CURRENT, '/', PageNumber.TOTAL_PAGES],
              font: FONT, size: SZ.body, color: COLOR.text,
            })],
          })],
        }),
      },
      children,
    }],
  });
}

// ---------- 실행 ----------
function main() {
  const [inputPath, outputPath] = process.argv.slice(2);
  if (!inputPath || !outputPath) {
    console.error('사용법: node build_meeting_note.js <input.json> <output.docx>');
    process.exit(1);
  }
  const data = JSON.parse(fs.readFileSync(inputPath, 'utf-8'));

  if (!data.executiveSummary || !data.executiveSummary.length) {
    console.error('경고: executiveSummary가 비어 있습니다. 하우스 양식은 소제목이 있는 Executive Summary를 요구합니다.');
  }

  const doc = build(data);
  Packer.toBuffer(doc)
    .then(buf => replaceStyles(buf))         // docx-js 기본 스타일 → 하우스 스타일로 교체
    .then(buf => {
      fs.writeFileSync(outputPath, buf);
      const nFoot = Object.keys(state.footnotes).length;
      console.log(`생성 완료: ${outputPath} (각주 ${nFoot}개)`);
    })
    .catch(err => {
      console.error('docx 생성 실패:', err);
      process.exit(1);
    });
}

// docx v9의 externalStyles는 기본 스타일과 병합되며 docDefaults가 유실되므로,
// 생성된 zip에서 word/styles.xml을 통째로 교체해 하우스 기본값을 보장한다.
function replaceStyles(buffer) {
  let JSZip;
  try {
    JSZip = require('jszip');
  } catch (e) {
    JSZip = require(path.join(path.dirname(require.resolve('docx/package.json')), 'node_modules', 'jszip'));
  }
  return JSZip.loadAsync(buffer).then(zip => {
    zip.file('word/styles.xml', STYLES_XML);
    return zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' });
  });
}

main();
