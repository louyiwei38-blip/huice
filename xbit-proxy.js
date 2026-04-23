/**
 * 解析代理行格式为标准 URL
 * 支持:
 *   host:port
 *   host:port:user:pass
 *   http://user:pass@host:port
 */
export function parseProxyLine(line) {
  if (!line) return null;
  line = line.trim();
  if (line.startsWith('http://') || line.startsWith('https://') || line.startsWith('socks5://')) {
    return line;
  }
  const parts = line.split(':');
  if (parts.length === 2) {
    return `http://${parts[0]}:${parts[1]}`;
  }
  if (parts.length === 4) {
    const [host, port, user, pass] = parts;
    return `http://${encodeURIComponent(user)}:${encodeURIComponent(pass)}@${host}:${port}`;
  }
  return null;
}
