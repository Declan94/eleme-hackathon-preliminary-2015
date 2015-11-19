#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import json
import urlparse
from time import time, sleep
from wsgiref.simple_server import make_server
from db_manager import get_db, get_redis_store

import cache
cache.cache_users_data()
cache.cache_foods_data()

############### special responses ###############

def my_response(data, status_code = 200, status = "ok"):
	r = {'status' : status,
		'status_code' : status_code}
	if data != None:
		if isinstance(data, str):
			r['data'] = data
		else:
			r['data'] = json.dumps(data)
	return r

def bad_req_1():
	return my_response({"code": "EMPTY_REQUEST", "message": "请求体为空"}, 400, "Bad Request")

def bad_req_2():
	return my_response({"code": "MALFORMED_JSON", "message": "格式错误"}, 400, "Bad Request")

def unauthorized():
	return my_response({"code": "INVALID_ACCESS_TOKEN", "message": "无效的令牌"}, 401, "Unauthorized")
	

############### support functions ###############

def check_data(request):
	data = request['data']
	if not data:
		return bad_req_1()
	else:
		try:
			data = json.loads(data)
		except Exception, e:
			return bad_req_2()
		else:
			return data

def authorize(request):
	args = request['args']
	temp = args.get('access_token', False)
	if temp and len(temp) > 0:
		access_token = temp[0]
	else:
		access_token = request.get('Access-Token')
		if not access_token:
			return unauthorized()
	return int(access_token)

# food relative #

def food_key(food_id, field = "stock"):
	return "dd.food%d.%s" % (food_id, field)

def food_field(food_id, field = "stock"):
	redis_store = get_redis_store()
	return int(redis_store.get(food_key(food_id, field)))

def food_exists(food_id):
	min_id = cache.food_min_id()
	max_id = cache.food_max_id()
	return food_id >= min_id and food_id <= max_id

# cart relative #

def cart_new(user_id):
	cart_id = "%f%d" % (time(), user_id)
	return cart_id

def cart_exists(cart_id):
	return float(cart_id) >= 0

def cart_belongs(cart_id, user_id):
	key = "dd.user%d.cart%s" %(user_id, cart_id)
	redis_store = get_redis_store()
	return  redis_store.get(key) == '1'

def cart_data(cart_id):
	k = "dd.cart%s" % cart_id
	# data = cache.get(k)
	# if data:
	# 	return data
	data = []
	redis_store = get_redis_store()
	fid_set = redis_store.smembers(k)
	data = [{'food_id': int(food_id), 'count': int(redis_store.get("dd.cart%s.count%s" % (cart_id, food_id)))} 
		for food_id in fid_set]
	# cache.cache(k, data)
	return data

def cart_len(cart_id):
	redis_store = get_redis_store()
	return redis_store.scard("dd.cart%s" % cart_id)

def cart_patch(cart_id, food_id, count):
	k = "dd.cart%s" % cart_id
	# cache.cache(k, None)
	redis_store = get_redis_store()
	redis_store.sadd(k, food_id)
	k2 = "dd.cart%s.count%d" % (cart_id, food_id)
	if redis_store.incrby(k2, count) < 0:
		redis_store.set(k2, 0)
	
# order relative #

def user_order_id(user_id):
	k = "dd.order%d" % user_id
	order_id = cache.get(k)
	if order_id:
		return order_id
	redis_store = get_redis_store()
	return redis_store.get(k)

def set_user_order_id(user_id, order_id):
	k = "dd.order%d" % user_id
	cache.cache(k, order_id)
	redis_store = get_redis_store()
	redis_store.set(k, order_id)

def user_order(user_id):
	order_id = user_order_id(user_id)
	if not order_id:
		return None
	cart = cart_data(order_id)
	total = sum([cache.food_price(item['food_id']) * item['count'] for item in cart])
	return {"id": order_id, "items": cart, "total": total}

def order_muti_foods(cart):
	for i in range(0, len(cart)):
		food_id = cart[i]['food_id']
		count = cart[i]['count']
		k = food_key(food_id)
		redis_store = get_redis_store()
		if redis_store.incrby(k, -count) < 0:
			for j in range(0, i+1):				
				redis_store.incrby(food_key(cart[j]['food_id'], cart[j]['count']))
			return False
	return True

def order_single_food(food):
	food_id = food['food_id']
	count = food['count']
	k = food_key(food_id)
	redis_store = get_redis_store()
	if redis_store.incrby(k, -count) < 0:
		redis_store.incrby(k, count)
		return False
	return True


############### view functions ###############

# @app.route('/login', methods=["POST"])
def login(request):
	data = check_data(request)
	if "status_code" in data:
		return data
	name = data.get('username', '')
	password = data.get('password', '')
	user_id = cache.check_user(name, password)
	if user_id != None:
		res_data = {'user_id': user_id, 'username': name, 'access_token': str(user_id)}
		return my_response(res_data)
	else:
		res_data = {"code": "USER_AUTH_FAIL", "message": "用户名或密码错误"}
		return my_response(res_data, 403, "Forbidden")

# @app.route('/foods')
def get_foods(request):
	user_id = authorize(request)
	if not isinstance(user_id, int):
		return user_id
	foods_json = cache.food_json()
	return my_response(foods_json)

# @app.route('/carts', methods=["POST"])
def new_carts(request):
	user_id = authorize(request)
	if not isinstance(user_id, int):
		return user_id
	cart_id = cart_new(user_id)
	return my_response({'cart_id': cart_id})

# @app.route('/carts/<cart_id>', methods=["PATCH"])
def patch_carts(request, cart_id):
	user_id = authorize(request)
	if not isinstance(user_id, int):
		return user_id
	data = check_data(request)
	if "status_code" in data:
		return data
	if not cart_exists(cart_id):
		return my_response({"code": "CART_NOT_FOUND", "message": "篮子不存在"}, 404, "Not Found")
	# if not cart_belongs(cart_id, user_id):
		# return my_response({"code": "NOT_AUTHORIZED_TO_ACCESS_CART", "message": "无权限访问指定的篮子"}, 401, "Unauthorized")
	food_id = int(data['food_id'])
	count = data['count']
	if not food_exists(food_id):
		return my_response({"code": "FOOD_NOT_FOUND", "message": "食物不存在"}, 404, "Not Found")
	total = count
	if total > 3:
		return my_response({"code": "FOOD_OUT_OF_LIMIT", "message": "篮子中食物数量超过了三个"}, 403, "Forbidden");
	# old = cart_data(cart_id)
	# if old:
	# 	for i in range(0, len(old)):
	# 		total = total + old[i]['count']
	# 		if total > 3:
	# 			return my_response({"code": "FOOD_OUT_OF_LIMIT", "message": "篮子中食物数量超过了三个"}, 403, "Forbidden");
	if not user_order_id(user_id):
		cart_patch(cart_id, food_id, count)
	return my_response(None, 204, "No content")

# @app.route('/orders', methods=["POST"])
def make_orders(request):
	user_id = authorize(request)
	if not isinstance(user_id, int):
		return user_id
	data = check_data(request)
	if "status_code" in data:
		return data
	cart_id = data['cart_id']
	if not cart_exists(cart_id):
		return my_response({"code": "CART_NOT_FOUND", "message": "篮子不存在"}, 404, "Not Found")
	# if not cart_belongs(cart_id, user_id):
		# return my_response({"code": "NOT_AUTHORIZED_TO_ACCESS_CART", "message": "无权限访问指定的篮子"}, 403, "Forbidden")
	if user_order_id(user_id) != None:
		return my_response({"code": "ORDER_OUT_OF_LIMIT", "message": "每个用户只能下一单"}, 403, "Forbidden")
	
	if cart_len(cart_id) == 1:
		cart = cart_data(cart_id)
		ret = order_single_food(cart[0])
	else:
		# cart = cart_data(cart_id)
		# ret = order_muti_foods(cart)
		ret = True

	if not ret:
		return my_response({"code": "FOOD_OUT_OF_STOCK", "message": "食物库存不足"}, 403, "Forbidden")

	order_id = cart_id
	set_user_order_id(user_id, order_id)
	return my_response({"id": order_id})

# @app.route('/orders')
def get_orders(request):
	user_id = authorize(request)
	if not isinstance(user_id, int):
		return user_id
	order = user_order(user_id)
	if not order:
		return my_response([])
	else:
		return my_response([order])

# @app.route('/admin/orders')
def all_orders(request):
	orders = []
	redis_store = get_redis_store()
	min_user_id = cache.user_min_id()
	max_user_id = cache.user_max_id()
	for user_id in range(min_user_id, max_user_id+1):
		order = user_order(user_id)
		if order:
			orders.append(order)
	return my_response(orders)


def try_app(environ, start_response):

	request = {
		'data': None,
		'Access-Token': None,
		'args': {}
	}

	try:
		request_body_size = int(environ.get('CONTENT_LENGTH', 0))
	except (ValueError):
		request_body_size = 0
	request['data'] = environ['wsgi.input'].read(request_body_size) if request_body_size > 0 else None
	request['Access-Token'] = environ.get('HTTP_ACCESS_TOKEN', None)
	request['args'] = urlparse.parse_qs(environ.get('QUERY_STRING', ''))
	
	path = environ['PATH_INFO'].strip()
	method = environ['REQUEST_METHOD'].strip()
	funcs = {
		'/login': login,
		'/foods': get_foods,
		'/admin/orders': all_orders
	}
	if path in funcs:
		r = funcs[path](request)
	else:
		if path[:6] == '/carts':
			if method == 'POST':
				r = new_carts(request)
			else:
				r = patch_carts(request, path[7:])
		if path[:7] == '/orders':
			if method == 'POST':
				r = make_orders(request)
			else:
				r = get_orders(request)
	status = "%d %s" % (r['status_code'], r['status'])
	response_body = r.get('data', '')
	response_headers = [('Content-Type', 'application/json'), 
		('Content-Length', str(len(response_body)))]  
	start_response(status, response_headers)  
	return [response_body]  

def app(environ, start_response):
	return try_app(environ, start_response)
	# try:
	# 	return try_app(environ, start_response)
	# except:
	# 	status = "500 Oops"
	# 	response_headers = [("content-type", "text/plain")]
	# 	start_response(status, response_headers, sys.exc_info())
	# 	return ["error body goes here"]


if __name__ == '__main__':
	host = os.getenv("APP_HOST", "localhost")
	port = int(os.getenv("APP_PORT", "8080"))
	httpd = make_server(host, port, app)
	print "Serving HTTP on %s:%d ..." % (host, port)
	# 开始监听HTTP请求:
	httpd.serve_forever()
