/**
 * Deploy all CDS models (QUANTIX + ODATANO plugin) to persistent SQLite.
 *
 * `cds deploy` only compiles the host app's own models — plugin tables
 * (SigningRequests, TransactionBuilds, …) are NOT deployed. This script
 * explicitly loads both model trees and creates every table.
 *
 * Usage: npm run deploy-db (from apps/cap/)
 */
const cds = require('@sap/cds');
const Database = require('better-sqlite3');
const fs = require('fs');
const path = require('path');

const DB_FILE = path.resolve(__dirname, '..', 'db.sqlite');
const DATA_DIR = path.resolve(__dirname, '..', 'db', 'data');

async function main() {
  process.chdir(path.resolve(__dirname, '..'));

  if (fs.existsSync(DB_FILE)) {
    fs.unlinkSync(DB_FILE);
    console.log('Removed old db.sqlite');
  }

  const appCSN = await cds.load(['db/schema.cds', 'srv/orders-service.cds']);
  const appSQL = cds.compile(appCSN).to.sql({ dialect: 'sqlite' });

  const odatanoCSN = await cds.load([
    'node_modules/@odatano/core/db/schema.cds',
    'node_modules/@odatano/core/srv/cardano-service.cds',
    'node_modules/@odatano/core/srv/cardano-tx-service.cds',
    'node_modules/@odatano/core/srv/cardano-sign-service.cds'
  ]);
  const odatanoSQL = cds.compile(odatanoCSN).to.sql({ dialect: 'sqlite' });

  const db = new Database(DB_FILE);
  const allSQL = [...appSQL, ...odatanoSQL];
  for (const stmt of allSQL) {
    try { db.exec(stmt); } catch (e) {
      if (!e.message.includes('already exists')) console.warn('DDL:', e.message.substring(0, 120));
    }
  }

  if (fs.existsSync(DATA_DIR)) {
    const csvFiles = fs.readdirSync(DATA_DIR).filter(f => f.endsWith('.csv'));
    for (const file of csvFiles) {
      const table = file.replace('.csv', '').replace(/[.-]/g, '_');
      const content = fs.readFileSync(path.join(DATA_DIR, file), 'utf8').trim();
      const lines = content.split('\n');
      const headers = lines[0].split(';');
      const cols = headers.map(h => '"' + h.trim() + '"').join(',');
      const placeholders = headers.map(() => '?').join(',');
      const insert = db.prepare(`INSERT OR IGNORE INTO ${table} (${cols}) VALUES (${placeholders})`);
      let count = 0;
      for (let i = 1; i < lines.length; i++) {
        if (!lines[i].trim()) continue;
        const values = lines[i].split(';').map(v => v.trim() === '' ? null : v.trim());
        insert.run(...values);
        count++;
      }
      console.log(`  ${file} → ${count} rows`);
    }
  }

  const tables = db.prepare("SELECT COUNT(*) as c FROM sqlite_master WHERE type='table'").get();
  const views = db.prepare("SELECT COUNT(*) as c FROM sqlite_master WHERE type='view'").get();
  console.log(`\nDone: ${tables.c} tables, ${views.c} views`);
  db.close();
}

main().catch(e => { console.error(e); process.exit(1); });
