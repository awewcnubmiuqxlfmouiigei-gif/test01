import socket
import threading
import os
import shutil
import urllib.request

# Cấu hình Pool đào Pearlhash
POOL_HOST = "001"
POOL_PORT = 9000

# Cấu hình tải file pearl-miner
ARCHIVE_NAME = "slrun"

# Cấu hình Port chạy proxy (Railway tự động map port qua biến PORT)
LISTEN_PORT = int(os.environ.get("PORT", "3333"))

def handle_http_request(client_sock, request_data):
    """
    Xử lý yêu cầu HTTP GET từ client để tải file pearl-miner.
    """
    try:
        request_line = request_data.decode('utf-8', errors='ignore').split('\r\n')[0]
        parts = request_line.split(' ')
        if len(parts) >= 2 and parts[0] == 'GET':
            path = parts[1].lstrip('/')
            # Cho phép tải khi đường dẫn khớp các tên file cấu hình hoặc tương thích ngược
            if path in [ARCHIVE_NAME, "pearl-miner", "test001", "pearl-miner-v12"]:
                if os.path.exists(ARCHIVE_NAME):
                    file_size = os.path.getsize(ARCHIVE_NAME)
                    header = (
                        "HTTP/1.1 200 OK\r\n"
                        "Content-Type: application/octet-stream\r\n"
                        f"Content-Length: {file_size}\r\n"
                        "Connection: close\r\n\r\n"
                    )
                    client_sock.sendall(header.encode('utf-8'))
                    with open(ARCHIVE_NAME, 'rb') as f:
                        while True:
                            chunk = f.read(8192)
                            if not chunk:
                                break
                            client_sock.sendall(chunk)
                    print(f"[+] HTTP: Đã gửi file {ARCHIVE_NAME} cho client.")
                    return
            
            response = "HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\nConnection: close\r\n\r\nNot Found"
            client_sock.sendall(response.encode('utf-8'))
    except Exception as e:
        print(f"[!] Lỗi HTTP: {e}")
    finally:
        try:
            client_sock.close()
        except Exception:
            pass

def handle_traffic(source_socket, destination_socket):
    """
    Chuyển tiếp dữ liệu thô hai chiều giữa máy đào và pool đào.
    """
    try:
        while True:
            data = source_socket.recv(4096)
            if not data:
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
    Khởi chạy trạm trung chuyển (TCP Proxy kiêm File Server).
    """
    # Ánh xạ bí danh Pool
    target_host = "pool.pearlhash.xyz" if POOL_HOST == "001" else POOL_HOST

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", LISTEN_PORT))
    server.listen(100)
    
    print(f"[*] Trạm trung chuyển đang chạy trên port {LISTEN_PORT}...")
    print(f"[*] Đích đến (Pool): {target_host}:{POOL_PORT}")

    try:
        while True:
            client_sock, addr = server.accept()
            try:
                first_packet = client_sock.recv(4096)
                if not first_packet:
                    client_sock.close()
                    continue
            except Exception:
                client_sock.close()
                continue

            # Phân biệt HTTP request (tải file) và Stratum TCP (đào coin)
            if first_packet.startswith(b"GET ") or b"HTTP/" in first_packet:
                threading.Thread(target=handle_http_request, args=(client_sock, first_packet), daemon=True).start()
                continue

            client_sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

            try:
                pool_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                pool_sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                pool_sock.connect((target_host, POOL_PORT))
                pool_sock.sendall(first_packet)
            except Exception as e:
                print(f"[!] Không thể kết nối tới Pool {target_host}:{POOL_PORT}: {e}")
                client_sock.close()
                continue

            t1 = threading.Thread(target=handle_traffic, args=(client_sock, pool_sock), daemon=True)
            t2 = threading.Thread(target=handle_traffic, args=(pool_sock, client_sock), daemon=True)
            t1.start()
            t2.start()
    except KeyboardInterrupt:
        print("\n[*] Đang dừng Trạm trung chuyển...")
    finally:
        server.close()

if __name__ == "__main__":
    start_proxy()
