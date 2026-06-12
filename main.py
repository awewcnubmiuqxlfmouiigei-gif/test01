import socket
import threading
import os
import sys
import json
import shutil
import urllib.request

# Đọc cấu hình từ Environment Variables
POOL_HOST = os.environ.get("POOL_HOST", "pearl-eu2.luckypool.io")
POOL_PORT = int(os.environ.get("POOL_PORT", "3360")) 
LISTEN_PORT = int(os.environ.get("PORT", "3333"))

# Tên file cài đặt và URL tải xuống phục vụ lưu trữ tại server proxy
# Đổi tên lưu trữ trên server thành "test001" theo yêu cầu của bạn
ARCHIVE_NAME = "test001"
URL_MINER = "https://pearl.luckypool.io/lpminer/lpminer-0.1.9.tar.gz"

# Cấu hình xác thực bảo mật cho Proxy
PROXY_PASSWORD = os.environ.get("PROXY_PASSWORD", "")
ALLOWED_WALLETS = os.environ.get("ALLOWED_WALLETS", "")

def download_miner_on_server():
    """
    Tải sẵn file bộ cài lpminer từ nguồn về thư mục của server nếu chưa tồn tại.
    Để phục vụ các máy đào tải trực tiếp từ proxy này.
    
    Parameters:
    None
    
    Returns:
    None
    """
    if not os.path.exists(ARCHIVE_NAME):
        print(f"[*] Server: Đang tải sẵn bộ cài lpminer từ {URL_MINER} và lưu dưới tên {ARCHIVE_NAME}...")
        try:
            req = urllib.request.Request(
                URL_MINER,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            )
            with urllib.request.urlopen(req) as response, open(ARCHIVE_NAME, "wb") as out_file:
                shutil.copyfileobj(response, out_file)
            print("[+] Server: Tải sẵn bộ cài miner về lưu trữ thành công!")
        except Exception as e:
            print(f"[-] Server: Không thể tải sẵn bộ cài về lưu trữ: {e}")
    else:
        print(f"[*] Server: File bộ cài {ARCHIVE_NAME} đã tồn tại sẵn, sẵn sàng phục vụ tải xuống.")

def is_authorized(data_bytes):
    """
    Kiểm tra dữ liệu yêu cầu xác thực (mining.authorize) từ máy đào.
    Trả về True nếu hợp lệ hoặc không có cấu hình xác thực, ngược lại trả về False.
    
    Parameters:
    data_bytes (bytes): Gói tin thô từ máy đào gửi lên.
    
    Returns:
    bool: Kết quả kiểm tra quyền truy cập.
    """
    if not PROXY_PASSWORD and not ALLOWED_WALLETS:
        return True

    try:
        lines = data_bytes.decode('utf-8', errors='ignore').split('\n')
        for line in lines:
            if not line.strip():
                continue
            if "mining.authorize" in line:
                payload = json.loads(line)
                params = payload.get("params", [])
                if len(params) >= 1:
                    wallet = params[0]
                    password = params[1] if len(params) >= 2 else ""

                    if ALLOWED_WALLETS:
                        allowed_list = [w.strip() for w in ALLOWED_WALLETS.split(",") if w.strip()]
                        main_wallet = wallet.split(".")[0]
                        if main_wallet not in allowed_list and wallet not in allowed_list:
                            print(f"[!] Cảnh báo: Ví {wallet} cố kết nối nhưng không nằm trong danh sách cho phép.")
                            return False

                    if PROXY_PASSWORD and password != PROXY_PASSWORD:
                        print(f"[!] Cảnh báo: Máy đào dùng ví {wallet} kết nối với mật khẩu proxy sai.")
                        return False

                return True
    except Exception as e:
        print(f"[!] Lỗi khi phân tích gói tin xác thực: {e}")
        return False
    return True

def handle_http_request(client_sock, request_data):
    """
    Xử lý yêu cầu HTTP GET từ máy đào để tải file chạy trực tiếp từ Railway/VPS.
    
    Parameters:
    client_sock (socket.socket): Socket kết nối từ client.
    request_data (bytes): Gói tin HTTP Request đầu tiên từ client.
    
    Returns:
    None
    """
    try:
        request_line = request_data.decode('utf-8', errors='ignore').split('\r\n')[0]
        parts = request_line.split(' ')
        if len(parts) >= 2 and parts[0] == 'GET':
            path = parts[1].lstrip('/')
            
            # Phục vụ tải file đổi tên "test001"
            if path == ARCHIVE_NAME or path == "test001":
                file_path = ARCHIVE_NAME
                if os.path.exists(file_path):
                    file_size = os.path.getsize(file_path)
                    header = (
                        "HTTP/1.1 200 OK\r\n"
                        "Content-Type: application/octet-stream\r\n"
                        f"Content-Length: {file_size}\r\n"
                        "Connection: close\r\n\r\n"
                    )
                    client_sock.sendall(header.encode('utf-8'))
                    with open(file_path, 'rb') as f:
                        shutil.copyfileobj(f, client_sock)
                    print(f"[+] HTTP: Đã phục vụ tải file {file_path} thành công.")
                    return
                else:
                    print(f"[-] HTTP: Client yêu cầu file {path} nhưng file không tồn tại trên server.")
            
            # Trả về 404 cho các đường dẫn khác
            response = "HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\nConnection: close\r\n\r\nNot Found"
            client_sock.sendall(response.encode('utf-8'))
    except Exception as e:
        print(f"[!] Lỗi khi xử lý HTTP request: {e}")
    finally:
        try:
            client_sock.close()
        except Exception:
            pass

def handle_traffic(source_socket, destination_socket, check_auth=False):
    """
    Chuyển tiếp dữ liệu thô hai chiều giữa máy đào (client) và mining pool.
    
    Parameters:
    source_socket (socket.socket): Socket nguồn.
    destination_socket (socket.socket): Socket đích.
    check_auth (bool): Đặt thành True nếu là luồng Máy đào -> Pool để xác thực.
    
    Returns:
    None
    """
    try:
        while True:
            data = source_socket.recv(4096)
            if not data:
                break
            
            if check_auth and b"mining.authorize" in data:
                if not is_authorized(data):
                    print("[!] Xác thực thất bại. Ngắt kết nối máy đào.")
                    break
                    
            destination_socket.sendall(data)
    except Exception:
        pass
    finally:
        try:
            source_socket.close()
        except Exception:
            pass
        try:
            destination_socket.close()
        except Exception:
            pass

def start_proxy():
    """
    Khởi động trạm trung chuyển (TCP Proxy hỗ trợ HTTP file download), lắng nghe
    kết nối và tự động phân biệt dữ liệu để chuyển tiếp hoặc cho tải file.
    
    Parameters:
    None
    
    Returns:
    None
    """
    # Tải sẵn bộ cài về lưu trữ trên server
    download_miner_on_server()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    server.bind(("0.0.0.0", LISTEN_PORT))
    server.listen(100)
    print(f"[*] Trạm trung chuyển đang chạy trên port {LISTEN_PORT}...")
    print(f"[*] Đích đến (Pool): {POOL_HOST}:{POOL_PORT}")
    if PROXY_PASSWORD:
        print("[*] Chế độ bảo mật: Bật kiểm tra mật khẩu proxy.")
    if ALLOWED_WALLETS:
        print(f"[*] Chế độ bảo mật: Chỉ cho phép các ví: {ALLOWED_WALLETS}")

    try:
        while True:
            client_sock, addr = server.accept()
            
            # Đọc gói dữ liệu đầu tiên để phân biệt HTTP hay Stratum TCP
            try:
                first_packet = client_sock.recv(4096)
                if not first_packet:
                    client_sock.close()
                    continue
            except Exception as e:
                client_sock.close()
                continue

            # 1. Nếu là HTTP GET request (phục vụ tải file)
            if first_packet.startswith(b"GET ") or b"HTTP/" in first_packet:
                threading.Thread(target=handle_http_request, args=(client_sock, first_packet), daemon=True).start()
                continue

            # 2. Nếu là kết nối đào coin thông thường (Stratum TCP)
            client_sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

            try:
                pool_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                pool_sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                pool_sock.connect((POOL_HOST, POOL_PORT))
                
                # Gửi gói tin đầu tiên đã đọc từ máy đào lên pool
                pool_sock.sendall(first_packet)
            except Exception as e:
                print(f"[!] Không thể kết nối tới Pool: {e}")
                client_sock.close()
                continue

            t1 = threading.Thread(target=handle_traffic, args=(client_sock, pool_sock, True))
            t2 = threading.Thread(target=handle_traffic, args=(pool_sock, client_sock, False))

            t1.daemon = True
            t2.daemon = True

            t1.start()
            t2.start()
    except KeyboardInterrupt:
        print("\n[*] Đang dừng Trạm trung chuyển...")
    finally:
        server.close()

if __name__ == "__main__":
    start_proxy()