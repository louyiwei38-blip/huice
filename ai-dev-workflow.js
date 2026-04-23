#!/usr/bin/env node
/**
 * AI-Assisted Development Workflow Orchestrator
 *
 * Stages:
 *   1. 研究阶段  - Scan codebase (Repomix), compress context, output summary
 *   2. 规划阶段  - Generate PLAN.md, review & approve loop
 *   3. 实施阶段  - Agent code writing, Git Worktree parallel racing
 *   4. 验证阶段  - Generate tests, run automated tests / type checks, retry loop
 *   5. 成功      - Merge code, final verification
 *
 * Usage:
 *   node scripts/ai-dev-workflow.js [options]
 *
 * Options:
 *   -r, --requirement <text>    Requirement description
 *   -d, --repo        <path>    Repository root path  (default: cwd)
 *   -b, --branch      <name>    Feature branch name   (default: auto)
 *       --test-cmd    <cmd>     Command to run tests
 *       --type-check  <cmd>     Command for type checking
 *       --worktrees   <n>       Number of parallel worktrees (default: 2)
 *       --max-retries <n>       Max validation retries      (default: 3)
 *       --no-worktree           Disable git worktree mode
 *   -y, --yes                   Auto-approve all prompts (CI mode)
 */

import { spawnSync } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import readline from 'node:readline/promises';
import { stdin as input, stdout as output } from 'node:process';

// ─── ANSI helpers ─────────────────────────────────────────────────────────────

const c = {
  reset:  '\x1b[0m',
  bold:   '\x1b[1m',
  dim:    '\x1b[2m',
  red:    '\x1b[31m',
  green:  '\x1b[32m',
  yellow: '\x1b[33m',
  blue:   '\x1b[34m',
  cyan:   '\x1b[36m',
  white:  '\x1b[37m',
};

function ts() {
  return new Date().toISOString().replace('T', ' ').slice(0, 19);
}

function log(phase, msg, color = c.cyan) {
  console.log(`${c.dim}[${ts()}]${c.reset} ${color}${c.bold}[${phase}]${c.reset} ${msg}`);
}

function section(title) {
  const bar = '─'.repeat(64);
  console.log(`\n${c.bold}${c.blue}${bar}${c.reset}`);
  console.log(`${c.bold}${c.blue}  ${title}${c.reset}`);
  console.log(`${c.bold}${c.blue}${bar}${c.reset}\n`);
}

// ─── CLI argument parsing ─────────────────────────────────────────────────────

function parseArgs() {
  const args = process.argv.slice(2);
  const opts = {
    requirement:   '',
    repoPath:      process.cwd(),
    branchName:    `feature/ai-dev-${Date.now()}`,
    testCmd:       '',
    typeCheckCmd:  '',
    maxRetries:    3,
    numWorktrees:  2,
    autoApprove:   false,
    noWorktree:    false,
    _planFeedback: '',
  };

  for (let i = 0; i < args.length; i++) {
    switch (args[i]) {
      case '-r': case '--requirement':  opts.requirement  = args[++i]; break;
      case '-d': case '--repo':         opts.repoPath     = path.resolve(args[++i]); break;
      case '-b': case '--branch':       opts.branchName   = args[++i]; break;
      case '--test-cmd':                opts.testCmd      = args[++i]; break;
      case '--type-check':              opts.typeCheckCmd = args[++i]; break;
      case '--worktrees':               opts.numWorktrees = parseInt(args[++i], 10); break;
      case '--max-retries':             opts.maxRetries   = parseInt(args[++i], 10); break;
      case '--no-worktree':             opts.noWorktree   = true; break;
      case '-y': case '--yes':          opts.autoApprove  = true; break;
    }
  }
  return opts;
}

// ─── Shell execution helper ───────────────────────────────────────────────────

function shell(cmd, cwd, { ignoreError = false } = {}) {
  const result = spawnSync(cmd, { shell: true, cwd, encoding: 'utf8' });
  const ok = result.status === 0;
  if (!ok && !ignoreError) {
    throw new Error(`Command failed (exit ${result.status}): ${cmd}\n${result.stderr || result.stdout}`);
  }
  return { ok, stdout: result.stdout ?? '', stderr: result.stderr ?? '' };
}

// ─── Readline helpers ─────────────────────────────────────────────────────────

async function ask(rl, question) {
  return (await rl.question(`${c.yellow}${c.bold}? ${c.reset}${c.yellow}${question}${c.reset} `)).trim();
}

async function confirm(rl, question, defaultYes = false) {
  const hint = defaultYes ? '[Y/n]' : '[y/N]';
  const answer = await ask(rl, `${question} ${hint}:`);
  if (answer === '') return defaultYes;
  return /^y/i.test(answer);
}

// ─── Phase 1: 研究阶段 ─────────────────────────────────────────────────────────

async function phaseResearch(opts, rl) {
  section('📚 研究阶段 (Research Phase)');

  const cacheDir = path.join(opts.repoPath, '.cache');
  await fs.mkdir(cacheDir, { recursive: true });

  // Step 1: Scan codebase
  log('Research', '扫描现有代码库 (Scanning codebase)...', c.cyan);

  const repomixOut = path.join(cacheDir, 'repomix-output.txt');
  const repomixResult = shell(
    `npx --yes repomix --output "${repomixOut}" --style plain`,
    opts.repoPath,
    { ignoreError: true },
  );

  let rawContext = '';
  if (repomixResult.ok) {
    log('Research', `Repomix 扫描完成 → ${repomixOut}`, c.green);
    rawContext = await fs.readFile(repomixOut, 'utf8').catch(() => '');
  } else {
    log('Research', 'Repomix 不可用，使用内置扫描 (fallback scan)...', c.yellow);
    rawContext = await fallbackScan(opts.repoPath);
  }

  // Step 2: Compress context (keep first 8000 chars as representative context)
  log('Research', '压缩上下文 (Compressing context)...', c.cyan);
  const compressedContext = rawContext.length > 8000
    ? rawContext.slice(0, 8000) + '\n\n...(truncated for context window)\n'
    : rawContext;

  // Step 3: Output current state summary
  log('Research', '输出现状摘要 (Writing summary)...', c.cyan);
  const summary = buildResearchSummary(opts, compressedContext);
  const summaryPath = path.join(cacheDir, 'research-summary.md');
  await fs.writeFile(summaryPath, summary, 'utf8');
  log('Research', `✅ 现状摘要已输出 → ${summaryPath}`, c.green);

  console.log(`\n${c.dim}${summary.slice(0, 600)}${summary.length > 600 ? '\n...' : ''}${c.reset}\n`);

  return { summaryPath, summary };
}

async function fallbackScan(repoPath) {
  const parts = [];

  const gitLog = shell('git log --oneline -20', repoPath, { ignoreError: true });
  if (gitLog.ok && gitLog.stdout.trim()) {
    parts.push(`## Recent Git Commits\n${gitLog.stdout.trim()}`);
  }

  const trackedFiles = shell(
    'git ls-files --cached --others --exclude-standard',
    repoPath,
    { ignoreError: true },
  );
  if (trackedFiles.ok && trackedFiles.stdout.trim()) {
    parts.push(`## Repository Files\n${trackedFiles.stdout.trim()}`);
  }

  try {
    const pkg = await fs.readFile(path.join(repoPath, 'package.json'), 'utf8');
    parts.push(`## package.json\n\`\`\`json\n${pkg.trim()}\n\`\`\``);
  } catch {}

  return parts.join('\n\n') || '(No scannable content found)';
}

function buildResearchSummary(opts, context) {
  return `# 现状摘要 (Research Summary)

**Generated**: ${new Date().toISOString()}
**Repository**: ${opts.repoPath}
**Requirement**: ${opts.requirement || '(pending definition)'}

---

## Codebase Context

${context}
`;
}

// ─── Phase 2: 规划阶段 ─────────────────────────────────────────────────────────

async function phasePlanning(opts, rl, researchResult) {
  section('📋 规划阶段 (Planning Phase)');

  const planPath = path.join(opts.repoPath, 'PLAN.md');
  let revision = 0;
  let approved = false;
  let planContent = '';

  while (!approved) {
    revision++;
    log('Planning', `生成 PLAN.md (revision ${revision})...`, c.cyan);

    planContent = buildPlan(opts, researchResult, revision);
    await fs.writeFile(planPath, planContent, 'utf8');
    log('Planning', `PLAN.md 已生成 → ${planPath}`, c.green);

    console.log(`\n${c.bold}${c.white}${'─'.repeat(40)} PLAN.md ${'─'.repeat(40)}${c.reset}`);
    console.log(planContent);
    console.log(`${c.bold}${c.white}${'─'.repeat(89)}${c.reset}\n`);

    if (opts.autoApprove) {
      log('Planning', '自动批准 (--yes 模式)', c.green);
      approved = true;
      break;
    }

    const action = await ask(rl, 'Review: (a)pprove / (r)eject / (e)dit  [approve]:');

    if (!action || action === 'a' || action === 'approve') {
      approved = true;
      log('Planning', '✅ 计划批准通过', c.green);
    } else if (action === 'r' || action === 'reject') {
      opts._planFeedback = await ask(rl, '请输入修改意见 (逻辑有误):');
      log('Planning', `计划被拒绝，重新生成... (${opts._planFeedback})`, c.red);
    } else if (action === 'e' || action === 'edit') {
      log('Planning', `请直接编辑 ${planPath}，完成后按 Enter`, c.yellow);
      await ask(rl, '编辑完成后按 Enter...');
      planContent = await fs.readFile(planPath, 'utf8');
      approved = true;
      log('Planning', '✅ 计划手动编辑并批准', c.green);
    } else {
      log('Planning', `未知选项 "${action}"，请重新输入`, c.yellow);
    }
  }

  return { planPath, planContent };
}

function buildPlan(opts, researchResult, revision) {
  const feedbackBlock = opts._planFeedback
    ? `\n> **Revision ${revision} — Feedback**: ${opts._planFeedback}\n`
    : '';

  const worktreeInfo = opts.noWorktree
    ? 'Single-branch mode (worktree disabled)'
    : `Parallel racing: ${opts.numWorktrees} git worktrees`;

  const testBlock = [
    opts.testCmd      ? `- Tests:      \`${opts.testCmd}\`` : '- Tests:      (set --test-cmd)',
    opts.typeCheckCmd ? `- Type check: \`${opts.typeCheckCmd}\`` : '- Type check: (set --type-check)',
  ].join('\n');

  return `# PLAN.md

**Generated**: ${new Date().toISOString()}
**Revision**: ${revision}
**Repository**: ${opts.repoPath}
**Branch**: \`${opts.branchName}\`
${feedbackBlock}
---

## Requirement

${opts.requirement || '> ⚠️  No requirement defined — fill this in.'}

## Research Summary

See \`.cache/research-summary.md\` for full codebase context.

## Implementation Steps

- [ ] **Step 1 — Setup**: Create branch \`${opts.branchName}\`, initialise worktrees
- [ ] **Step 2 — Core logic**: Implement the primary feature
- [ ] **Step 3 — Edge cases & error handling**
- [ ] **Step 4 — Tests**: Write unit/integration tests
- [ ] **Step 5 — Review & merge**

## Worktree Strategy

${worktreeInfo}

## Verification Commands

${testBlock}

## Acceptance Criteria

- [ ] All automated tests pass
- [ ] Type checks pass (if applicable)
- [ ] Code reviewed and approved
- [ ] Merged to main branch
`;
}

// ─── Phase 3: 实施阶段 ────────────────────────────────────────────────────────

async function phaseImplementation(opts, rl, planResult) {
  section('🛠  实施阶段 (Implementation Phase)');

  // Ensure feature branch exists
  log('Implement', `创建/切换功能分支: ${opts.branchName}`, c.cyan);
  const branchExists = shell(
    `git show-ref --verify --quiet refs/heads/${opts.branchName}`,
    opts.repoPath,
    { ignoreError: true },
  );
  if (!branchExists.ok) {
    shell(`git checkout -b ${opts.branchName}`, opts.repoPath);
    log('Implement', `✅ 分支已创建: ${opts.branchName}`, c.green);
  } else {
    shell(`git checkout ${opts.branchName}`, opts.repoPath);
    log('Implement', `分支已存在，已切换: ${opts.branchName}`, c.dim);
  }

  // Git Worktree — parallel racing
  const worktreePaths = [];

  if (!opts.noWorktree && opts.numWorktrees > 1) {
    log('Implement', `Git Worktree 并行赛马: 建立 ${opts.numWorktrees} 个 worktrees...`, c.cyan);

    for (let i = 1; i <= opts.numWorktrees; i++) {
      const wtBranch = `${opts.branchName}-wt${i}`;
      const wtDir    = path.join(opts.repoPath, '..', `wt-${path.basename(opts.repoPath)}-${i}`);

      shell(`git worktree remove --force "${wtDir}"`, opts.repoPath, { ignoreError: true });
      shell(`git branch -D ${wtBranch}`,              opts.repoPath, { ignoreError: true });
      shell(`git worktree add "${wtDir}" -b ${wtBranch}`, opts.repoPath);

      worktreePaths.push({ path: wtDir, branch: wtBranch, index: i });
      log('Implement', `✅ Worktree ${i}: ${wtDir}  →  ${wtBranch}`, c.green);
    }

    console.log(`\n${c.bold}${c.white}并行 Worktrees 就绪:${c.reset}`);
    for (const wt of worktreePaths) {
      console.log(`  ${c.cyan}[WT${wt.index}]${c.reset} ${wt.path}  (branch: ${c.yellow}${wt.branch}${c.reset})`);
    }
    console.log();
  }

  // Agent code-writing prompt
  log('Implement', 'Agent 编写代码阶段 (Code writing)...', c.cyan);

  if (!opts.autoApprove) {
    console.log(`\n${c.yellow}${c.bold}📌 请根据 PLAN.md 实施代码:${c.reset}`);
    console.log(`   计划: ${planResult.planPath}`);
    console.log(`   需求: ${opts.requirement || '(见 PLAN.md)'}`);
    if (worktreePaths.length > 0) {
      console.log(`   可用 Worktrees:`);
      for (const wt of worktreePaths) {
        console.log(`     [WT${wt.index}] ${wt.path}`);
      }
    }
    console.log();
    await ask(rl, '代码实施完成后按 Enter 进入验证阶段...');
  } else {
    log('Implement', 'CI 模式: 跳过手动代码编写提示', c.dim);
  }

  return { worktreePaths };
}

// ─── Phase 4: 验证阶段 ────────────────────────────────────────────────────────

async function phaseValidation(opts, rl, implResult) {
  section('✅ 验证阶段 (Validation Phase)');

  let passed  = false;
  let attempt = 0;

  while (!passed && attempt <= opts.maxRetries) {
    if (attempt > 0) {
      log('Validate', `第 ${attempt}/${opts.maxRetries} 次重试 (报错 → 回到实施阶段)...`, c.yellow);
      if (!opts.autoApprove) {
        const cont = await confirm(rl, '已修复问题? 重新运行验证?', true);
        if (!cont) { log('Validate', '用户中止重试', c.red); break; }
      }
    }

    // Step 1: Generate test stub if none exists
    log('Validate', '生成测试 (Generate tests)...', c.cyan);
    await ensureTestStub(opts);

    // Step 2: Run automated tests
    const testPassed = await runCheck('测试 (Tests)', opts.testCmd, opts.repoPath);

    // Step 3: Type checking
    const typePassed = await runCheck('类型检查 (Type Check)', opts.typeCheckCmd, opts.repoPath);

    const autoChecksOk = testPassed && typePassed;
    const noAutoChecks = !opts.testCmd && !opts.typeCheckCmd;

    if (noAutoChecks && !opts.autoApprove) {
      const ok = await confirm(rl, '验证通过了吗?', false);
      passed = ok;
    } else {
      passed = autoChecksOk || (noAutoChecks && opts.autoApprove);
    }

    if (passed) {
      log('Validate', '🎉 验证通过 (Validation passed)!', c.green);
    } else {
      log('Validate', '❌ 验证失败 (Validation failed)', c.red);
      attempt++;
    }
  }

  if (!passed) {
    log('Validate', `已达最大重试次数 (${opts.maxRetries})，工作流中止`, c.red);
  }

  return { passed };
}

async function runCheck(label, cmd, cwd) {
  if (!cmd) {
    log('Validate', `${label}: 未配置，跳过`, c.dim);
    return true;
  }

  log('Validate', `运行 ${label}: ${cmd}`, c.cyan);
  const result = shell(cmd, cwd, { ignoreError: true });

  if (result.ok) {
    log('Validate', `✅ ${label} 通过`, c.green);
    if (result.stdout.trim()) console.log(`${c.dim}${result.stdout.trim()}${c.reset}`);
    return true;
  }

  log('Validate', `❌ ${label} 失败 (报错)`, c.red);
  if (result.stderr.trim()) console.error(`${c.red}${result.stderr.trim()}${c.reset}`);
  if (result.stdout.trim()) console.log(result.stdout.trim());
  return false;
}

async function ensureTestStub(opts) {
  const testDir  = path.join(opts.repoPath, 'tests');
  const testFile = path.join(testDir, 'ai-workflow-generated.test.js');

  try {
    await fs.access(testFile);
    log('Validate', `测试文件已存在: ${testFile}`, c.dim);
  } catch {
    await fs.mkdir(testDir, { recursive: true });
    const stub = [
      `// Auto-generated test stub — ai-dev-workflow.js`,
      `// Requirement : ${opts.requirement || 'N/A'}`,
      `// Generated   : ${new Date().toISOString()}`,
      ``,
      `import assert from 'node:assert/strict';`,
      `import { describe, it } from 'node:test';`,
      ``,
      `describe('AI Dev Workflow — Generated Stub', () => {`,
      `  it('should pass basic sanity check', () => {`,
      `    assert.ok(true, 'Replace this with real assertions');`,
      `  });`,
      ``,
      `  // TODO: Add tests for: ${opts.requirement || 'your feature'}`,
      `});`,
      ``,
    ].join('\n');

    await fs.writeFile(testFile, stub, 'utf8');
    log('Validate', `✅ 测试存根已生成 → ${testFile}`, c.green);
  }
}

// ─── Phase 5: 成功 ────────────────────────────────────────────────────────────

async function phaseSuccess(opts, rl, implResult) {
  section('🎊 成功 (Success)');

  // Worktree racing: pick winner and merge
  if (implResult.worktreePaths.length > 0) {
    log('Success', 'Git Worktree 并行赛马 — 选择最佳实现...', c.cyan);

    let winner = null;

    if (!opts.autoApprove) {
      console.log(`\n${c.bold}可用 Worktrees:${c.reset}`);
      for (const wt of implResult.worktreePaths) {
        const diff = shell(
          `git diff --stat ${opts.branchName}...${wt.branch}`,
          opts.repoPath,
          { ignoreError: true },
        );
        console.log(`  ${c.cyan}[${wt.index}]${c.reset} ${wt.branch}`);
        if (diff.ok && diff.stdout.trim()) {
          console.log(`      ${c.dim}${diff.stdout.trim()}${c.reset}`);
        }
      }
      console.log();

      const choice = await ask(
        rl,
        `选择要合并的 Worktree 编号 (1–${implResult.worktreePaths.length}), 或 's' 跳过 [1]:`,
      );

      if (choice !== 's' && choice !== 'skip') {
        const idx = parseInt(choice || '1', 10);
        winner = implResult.worktreePaths.find((wt) => wt.index === idx)
               ?? implResult.worktreePaths[0];
      }
    } else {
      winner = implResult.worktreePaths[0];
    }

    if (winner) {
      log('Success', `合并 Worktree ${winner.index} (${winner.branch}) → ${opts.branchName}`, c.cyan);
      shell(`git checkout ${opts.branchName}`, opts.repoPath);
      const mergeResult = shell(
        `git merge --no-ff ${winner.branch} -m "merge: worktree ${winner.branch} (AI parallel racing winner)"`,
        opts.repoPath,
        { ignoreError: true },
      );
      if (mergeResult.ok) {
        log('Success', `✅ Worktree ${winner.index} 合并成功`, c.green);
      } else {
        log('Success', `⚠️  合并冲突，请手动解决:\n${mergeResult.stderr}`, c.yellow);
      }
    }

    // Cleanup all worktrees
    for (const wt of implResult.worktreePaths) {
      shell(`git worktree remove --force "${wt.path}"`, opts.repoPath, { ignoreError: true });
      shell(`git branch -D ${wt.branch}`,               opts.repoPath, { ignoreError: true });
    }
    log('Success', '🧹 Worktrees 已清理', c.dim);
  }

  // Merge feature branch → main
  const mainBranch = detectMainBranch(opts.repoPath);
  log('Success', `合并代码: ${opts.branchName} → ${mainBranch}`, c.cyan);

  const doMerge = opts.autoApprove
    ? false
    : await confirm(rl, `是否将 ${opts.branchName} 合并到 ${mainBranch}?`, false);

  if (doMerge) {
    shell(`git checkout ${mainBranch}`, opts.repoPath);
    const mergeResult = shell(
      `git merge --no-ff ${opts.branchName} -m "feat: ${opts.requirement || opts.branchName} (AI-assisted)"`,
      opts.repoPath,
      { ignoreError: true },
    );
    if (mergeResult.ok) {
      log('Success', `✅ 已合并到 ${mainBranch}`, c.green);
    } else {
      log('Success', `⚠️  合并需要手动处理:\n${mergeResult.stderr}`, c.yellow);
    }
  } else {
    log('Success', `保留分支 ${opts.branchName}，未合并到 ${mainBranch}`, c.dim);
  }

  // Final verification
  log('Success', '最终验证 (Final verification)...', c.cyan);
  if (opts.testCmd) {
    const finalTest = shell(opts.testCmd, opts.repoPath, { ignoreError: true });
    if (finalTest.ok) {
      log('Success', '✅ 最终验证通过', c.green);
    } else {
      log('Success', '⚠️  最终验证失败，请手动检查', c.yellow);
      if (finalTest.stderr.trim()) console.error(finalTest.stderr.trim());
    }
  } else {
    log('Success', '未配置测试命令，跳过最终验证', c.dim);
  }

  section('🏁 工作流完成 (Workflow Complete)');
  console.log(`${c.bold}${c.green}  ✅ AI 辅助开发工作流已完成!${c.reset}`);
  console.log(`  需求    : ${opts.requirement || '(见 PLAN.md)'}`);
  console.log(`  分支    : ${opts.branchName}`);
  console.log(`  计划文件: ${path.join(opts.repoPath, 'PLAN.md')}`);
  console.log(`  摘要文件: ${path.join(opts.repoPath, '.cache', 'research-summary.md')}`);
  console.log();
}

function detectMainBranch(repoPath) {
  const symRef = shell(
    'git symbolic-ref refs/remotes/origin/HEAD',
    repoPath,
    { ignoreError: true },
  );
  if (symRef.ok && symRef.stdout.trim()) {
    return symRef.stdout.trim().replace('refs/remotes/origin/', '').trim();
  }
  const hasMain = shell('git show-ref --verify refs/heads/main', repoPath, { ignoreError: true });
  return hasMain.ok ? 'main' : 'master';
}

// ─── Entry point ──────────────────────────────────────────────────────────────

async function main() {
  const opts = parseArgs();
  const rl   = readline.createInterface({ input, output, terminal: true });

  try {
    section('🤖 AI 辅助开发工作流 (AI-Assisted Development Workflow)');
    log('Init', `Repository : ${opts.repoPath}`, c.white);
    log('Init', `Branch     : ${opts.branchName}`, c.white);
    log('Init', `Worktrees  : ${opts.noWorktree ? 'disabled' : opts.numWorktrees}`, c.white);
    log('Init', `Max retries: ${opts.maxRetries}`, c.white);

    // 定义需求 — prompt if not supplied via CLI
    if (!opts.requirement) {
      opts.requirement = await ask(rl, '请输入需求描述 (定义需求):');
    }
    log('Init', `Requirement: ${opts.requirement}`, c.white);

    // ── Phase 1: 研究阶段
    const researchResult = await phaseResearch(opts, rl);

    // ── Phase 2: 规划阶段
    const planResult = await phasePlanning(opts, rl, researchResult);

    // ── Phase 3: 实施阶段
    const implResult = await phaseImplementation(opts, rl, planResult);

    // ── Phase 4: 验证阶段
    const { passed } = await phaseValidation(opts, rl, implResult);

    if (!passed) {
      log('Workflow', '❌ 验证未通过，工作流终止', c.red);
      process.exitCode = 1;
      return;
    }

    // ── Phase 5: 成功
    await phaseSuccess(opts, rl, implResult);

  } finally {
    rl.close();
  }
}

main().catch((err) => {
  console.error(`\n${c.red}${c.bold}[Fatal Error]${c.reset} ${err?.message || err}`);
  process.exit(1);
});
