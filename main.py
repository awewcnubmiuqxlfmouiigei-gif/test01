import socket
import threading
import os
import sys
import json

# Đọc cấu hình từ Environment Variables
POOL_HOST = os.environ.get("POOL_HOST", "pearl-eu2.luckypool.io")
POOL_PORT = int(os.environ.get("POOL_PORT", "3360")) 
LISTEN_PORT = int(os.environ.get("PORT", "3333"))

# Cấu hình xác thực bảo mật cho Proxy
# Đặt PROXY_PASSWORD trong .env/Railway. Máy đào cần chạy với tham số mật khẩu tương ứng (ví dụ: -p mật_khẩu)
PROXY_PASSWORD = os.environ.get("PROXY_PASSWORD", "")
# Đặt danh sách ví được phép đào qua proxy (cách nhau bằng dấu phẩy, ví dụ: ví_1,ví_2)
ALLOWED_WALLETS = os.environ.get("ALLOWED_WALLETS", "")

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
        # Giải mã dữ liệu và xử lý từng dòng JSON-RPC (Stratum phân tách bằng dòng mới \n)
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

                    # 1. Kiểm tra danh sách ví được phép (nếu có cấu hình)
                    if ALLOWED_WALLETS:
                        allowed_list = [w.strip() for w in ALLOWED_WALLETS.split(",") if w.strip()]
                        # Tách lấy phần địa chỉ ví chính (bỏ qua tên worker sau dấu chấm ví dụ: wallet.worker1)
                        main_wallet = wallet.split(".")[0]
                        if main_wallet not in allowed_list and wallet not in allowed_list:
                            print(f"[!] Cảnh báo: Ví {wallet} cố kết nối nhưng không nằm trong danh sách cho phép.")
                            return False

                    # 2. Kiểm tra mật khẩu Proxy (nếu có cấu hình)
                    if PROXY_PASSWORD and password != PROXY_PASSWORD:
                        print(f"[!] Cảnh báo: Máy đào dùng ví {wallet} kết nối với mật khẩu proxy sai.")
                        return False

                return True
    except Exception as e:
        # Nếu lỗi parse (dữ liệu sai cấu trúc hoặc lỗi mã hóa), từ chối để an toàn
        print(f"[!] Lỗi khi phân tích gói tin xác thực: {e}")
        return False
    return True

def handle_traffic(source_socket, destination_socket, check_auth=False):
    """
    Chuyển tiếp dữ liệu thô hai chiều giữa máy đào (client) và mining pool.
    Đồng thời giám sát và chặn kết nối nếu không vượt qua kiểm tra xác thực.
    
    Parameters:
    source_socket (socket.socket): Socket nguồn nhận dữ liệu.
    destination_socket (socket.socket): Socket đích nhận dữ liệu chuyển tiếp.
    check_auth (bool): Đặt thành True nếu là luồng Máy đào -> Pool để kiểm tra quyền.
    
    Returns:
    None
    """
    try:
        while True:
            data = source_socket.recv(4096)
            if not data:
                break
            
            # Kiểm tra quyền nếu là gói tin authorization của máy đào
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
    Khởi động trạm trung chuyển (TCP Proxy), lắng nghe kết nối từ các máy đào
    và tạo luồng chuyển tiếp dữ liệu song song hai chiều tới Pool.
    
    Parameters:
    None
    
    Returns:
    None
    """
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
            print(f"[*] Nhận kết nối từ máy đào: {addr[0]}:{addr[1]}")

            client_sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

            try:
                pool_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                pool_sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                pool_sock.connect((POOL_HOST, POOL_PORT))
            except Exception as e:
                print(f"[!] Không thể kết nối tới Pool: {e}")
                client_sock.close()
                continue

            # Luồng 1: Máy đào -> Pool (Bật check_auth=True để giám sát gói tin gửi lên)
            t1 = threading.Thread(target=handle_traffic, args=(client_sock, pool_sock, True))
            # Luồng 2: Pool -> Máy đào (Không cần check_auth)
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