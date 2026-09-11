import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const root = new URL("../src/", import.meta.url).pathname.replace(/^\/(.:\/)/, "$1");
const errors = [];

function filesUnder(directory) {
  if (!existsSync(directory)) return [];
  return readdirSync(directory).flatMap((name) => {
    const path = join(directory, name);
    return statSync(path).isDirectory() ? filesUnder(path) : [path];
  });
}

const files = filesUnder(root).filter((file) => /\.(ts|tsx)$/.test(file));
for (const file of files) {
  const source = readFileSync(file, "utf8");
  const name = relative(root, file).replaceAll(String.fromCharCode(92), "/");

  if (source.includes("__FRONTEND_FRAMEWORK_IMPORT__")) {
    errors.push(name + ": 含占位导入");
  }
  if (/\bfetch\s*\(/.test(source) && name !== "shared/api/httpClient.ts") {
    errors.push(name + ": 直接调用fetch，应通过shared/api/httpClient");
  }
  if (/["']@\/(api|components|domain|services|stores|utils)\//.test(source)) {
    errors.push(name + ": 引用了已废弃的横向目录");
  }
  if (/\bsetInterval\s*\(/.test(source) && !name.startsWith("shared/hooks/")) {
    errors.push(name + ": 页面或组件不得自行创建轮询定时器，应使用shared/hooks");
  }
  if (name.startsWith("shared/") && /["']@\/modules\//.test(source)) {
    errors.push(name + ": shared不得依赖业务模块");
  }
  if (name.includes("/screens/")) {
    if (/\b(useWorkflowActions|useAsyncResource|requestJson|requestUrlResponse)\b/.test(source)) {
      errors.push(name + ": Screen不得直接执行请求或通用Workflow，应通过模块Hook/Controller");
    }
    for (const match of source.matchAll(/import\s+([\s\S]*?)\s+from\s+["']([^"']+)["'];/g)) {
      const clause = match[1].trim();
      const target = match[2];
      if (/(?:api|Api)$/.test(target) && !clause.startsWith("type ")) {
        errors.push(name + ": Screen只能从API模块导入类型，数据操作应下沉到模块Hook/Controller");
      }
    }
  }  if (name.startsWith("pages/")) {
    const lines = source.split(/\r?\n/).length;
    if (lines > 15) errors.push(name + ": 路由页面超过15行，应下沉到模块Screen");
    if (/\b(useState|useEffect|requestJson|fetch)\b/.test(source)) {
      errors.push(name + ": 路由页面包含业务状态或请求逻辑");
    }
  }
}

for (const directory of ["api", "components", "domain", "services", "stores", "utils"]) {
  const remaining = filesUnder(join(root, directory)).filter((file) => /\.(ts|tsx)$/.test(file));
  if (remaining.length) errors.push(directory + "/: 旧目录仍有源码文件");
}

if (errors.length) {
  console.error("前端架构检查失败：\n- " + errors.join("\n- "));
  process.exit(1);
}

console.log("前端架构检查通过");
