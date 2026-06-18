// 공사 저장소 — 브라우저 IndexedDB 기반 (서버 저장 없음).
// 한 PC·한 브라우저에 묶이며, 마지막 저장일 + 60일 후 목록 조회 시 자동 삭제.
// 저장 항목 구조:
//   { key, company, project_name, project_info, all_results,
//     file_names: {doc_id: [name, ...]},
//     files:      {doc_id: [{name, b64}, ...]},
//     saved_at:   <epoch ms> }
(function (global) {
  var DB_NAME = 'kwcgc_projects';
  var STORE = 'projects';
  var DB_VERSION = 1;
  var TTL_MS = 60 * 24 * 60 * 60 * 1000; // 60일

  function openDB() {
    return new Promise(function (resolve, reject) {
      var req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = function () {
        var db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          db.createObjectStore(STORE, { keyPath: 'key' });
        }
      };
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error); };
    });
  }

  // 회사명 + 공사명 정규화 키 (공백 제거 + 소문자) — 같은 공사면 덮어쓰기.
  function normKey(company, name) {
    var c = (company || '').replace(/\s+/g, '').toLowerCase();
    var n = (name || '').replace(/\s+/g, '').toLowerCase();
    return c + '||' + n;
  }

  var ProjectDB = {
    // 검토 직후 1회 저장 (같은 공사명이면 덮어쓰기).
    save: function (data, nowMs) {
      var entry = {
        key: normKey(data.company, data.project_name),
        company: data.company || '',
        project_name: data.project_name || '',
        project_info: data.project_info || {},
        all_results: data.all_results || {},
        file_names: data.file_names || {},
        files: data.files || {},
        saved_at: nowMs
      };
      return openDB().then(function (db) {
        return new Promise(function (resolve, reject) {
          var t = db.transaction(STORE, 'readwrite');
          t.objectStore(STORE).put(entry);
          t.oncomplete = function () { resolve(entry.key); };
          t.onerror = function () { reject(t.error); };
        });
      });
    },

    // 60일 지난 항목을 정리한 뒤, 목록 메타(files 제외)를 최신순으로 반환.
    list: function (nowMs) {
      return openDB().then(function (db) {
        return new Promise(function (resolve, reject) {
          var t = db.transaction(STORE, 'readwrite');
          var store = t.objectStore(STORE);
          var out = [];
          var ga = store.getAll();
          ga.onsuccess = function () {
            (ga.result || []).forEach(function (e) {
              if (nowMs - (e.saved_at || 0) > TTL_MS) {
                store.delete(e.key);
                return;
              }
              var cnt = 0, fn = e.file_names || {};
              Object.keys(fn).forEach(function (d) { cnt += (fn[d] || []).length; });
              out.push({
                key: e.key,
                company: e.company,
                project_name: e.project_name,
                saved_at: e.saved_at,
                file_count: cnt
              });
            });
          };
          t.oncomplete = function () {
            out.sort(function (a, b) { return (b.saved_at || 0) - (a.saved_at || 0); });
            resolve(out);
          };
          t.onerror = function () { reject(t.error); };
        });
      });
    },

    // 단건 전체(파일 포함) 조회 — 불러오기용.
    get: function (key) {
      return openDB().then(function (db) {
        return new Promise(function (resolve, reject) {
          var g = db.transaction(STORE, 'readonly').objectStore(STORE).get(key);
          g.onsuccess = function () { resolve(g.result || null); };
          g.onerror = function () { reject(g.error); };
        });
      });
    },

    // 즉시 삭제.
    remove: function (key) {
      return openDB().then(function (db) {
        return new Promise(function (resolve, reject) {
          var t = db.transaction(STORE, 'readwrite');
          t.objectStore(STORE).delete(key);
          t.oncomplete = function () { resolve(true); };
          t.onerror = function () { reject(t.error); };
        });
      });
    }
  };

  global.ProjectDB = ProjectDB;
})(window);
