// =============================================================
//  Apple 极简白 · pure.js
//  复用 ../js/data.js（PRODUCTS / CART_ITEMS / ORDERS / ADDRESSES）
// =============================================================

const Pure = (() => {
  const $  = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const fmt = n => '¥' + Number(n).toLocaleString('zh-CN');

  const CART_KEY = 'PURE_CART_V1';

  // ---- 购物车 ----
  function getCart() {
    try { return JSON.parse(localStorage.getItem(CART_KEY)) || []; }
    catch { return []; }
  }
  function saveCart(c) {
    localStorage.setItem(CART_KEY, JSON.stringify(c));
    updateCartBadge();
  }
  function addToCart(productId, qty = 1) {
    const c = getCart();
    const p = PRODUCTS.find(x => x.id === productId);
    if (!p) return;
    const idx = c.findIndex(x => x.productId === productId);
    if (idx >= 0) c[idx].qty += qty;
    else c.push({
      id: Date.now(),
      productId: p.id,
      name: p.name,
      spec: '默认规格',
      price: p.price,
      qty,
      checked: true,
      emoji: p.emoji,
    });
    saveCart(c);
  }
  function removeCart(id) { saveCart(getCart().filter(x => x.id !== id)); }
  function updateQty(id, delta) {
    const c = getCart();
    const it = c.find(x => x.id === id);
    if (!it) return;
    it.qty = Math.max(1, it.qty + delta);
    saveCart(c);
  }
  function toggleCheck(id, checked) {
    const c = getCart();
    const it = c.find(x => x.id === id);
    if (it) { it.checked = checked; saveCart(c); }
  }
  function toggleAll(checked) {
    getCart().forEach(it => it.checked = checked);
    saveCart(getCart());
  }
  function cartTotal() {
    const c = getCart().filter(x => x.checked);
    return {
      count: c.reduce((s, x) => s + x.qty, 0),
      sum:   c.reduce((s, x) => s + x.qty * x.price, 0),
    };
  }
  function updateCartBadge() {
    $$('.cart-link .count').forEach(el => {
      el.textContent = cartTotal().count;
    });
  }

  // ---- 商品卡 ----
  function productCardHTML(p) {
    let tagHTML = '';
    if (p.tag === '新品') tagHTML = `<span class="tag new">新品</span>`;
    else if (p.tag === '热销') tagHTML = `<span class="tag hot">热销</span>`;
    else if (p.tag === '推荐') tagHTML = `<span class="tag">推荐</span>`;
    return `
      <div class="product-card">
        <div class="photo">${p.emoji}</div>
        <div class="tag-row">${tagHTML}</div>
        <h3>${p.name}</h3>
        <div class="price-row">
          <span class="price"><span class="sym">¥</span>${p.price.toLocaleString()} 起</span>
        </div>
        ${p.oldPrice ? `<div class="meta"><span style="text-decoration:line-through;color:var(--color-text-3)">¥${p.oldPrice.toLocaleString()}</span></div>` : ''}
        <div class="links">
          <a class="btn-link" href="product-detail.html?id=${p.id}">了解</a>
          <button class="add-btn" data-add="${p.id}">购买</button>
        </div>
      </div>
    `;
  }
  function renderProductGrid(el, list) {
    if (!el) return;
    el.innerHTML = list.map(productCardHTML).join('');
    $$('.add-btn', el).forEach(btn => {
      btn.addEventListener('click', () => {
        addToCart(+btn.dataset.add, 1);
        flashToast('已加入购物车');
      });
    });
  }

  // ---- Toast（蓝色 pill） ----
  function flashToast(msg) {
    let t = $('#pure-toast');
    if (!t) {
      t = document.createElement('div');
      t.id = 'pure-toast';
      Object.assign(t.style, {
        position: 'fixed', bottom: '32px', left: '50%',
        transform: 'translateX(-50%)',
        padding: '12px 24px',
        background: 'rgba(29, 29, 31, .92)',
        backdropFilter: 'blur(20px)',
        color: '#fff',
        fontFamily: '-apple-system, "SF Pro Text", sans-serif',
        fontSize: '15px',
        borderRadius: '980px',
        zIndex: '9999',
        opacity: '0', transition: 'opacity .25s, transform .25s',
      });
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.style.opacity = '1';
    clearTimeout(t._tm);
    t._tm = setTimeout(() => t.style.opacity = '0', 1600);
  }

  // ---- 分类 ----
  const CAT_DEFS = [
    { name: 'ELEC',   cn: '电子', glyph: '📱', label: 'STORE',  link: '选购 ELEC' },
    { name: 'HOME',   cn: '家居', glyph: '🏠', label: 'HOME',   link: '选购 家居' },
    { name: 'STYLE',  cn: '服饰', glyph: '👟', label: 'STYLE',  link: '选购 服饰' },
    { name: 'BEAUTY', cn: '美妆', glyph: '💄', label: 'BEAUTY', link: '选购 美妆' },
  ];
  function renderCategories(el) {
    if (!el) return;
    el.innerHTML = CAT_DEFS.map(c => `
      <a class="category-card" href="products.html?cat=${c.name}">
        <div class="name">${c.label}.</div>
        <div class="cn">${c.cn} · 全新系列</div>
        <div class="links">
          <a class="btn-link" href="products.html?cat=${c.name}">${c.link}</a>
          <a class="btn-link" href="products.html?cat=${c.name}">了解更多</a>
        </div>
        <div class="glyph">${c.glyph}</div>
      </a>
    `).join('');
  }

  // ---- 顶部条 ----
  function renderMarquee(el) {
    if (!el) return;
    el.innerHTML = `免费送货 · 7 天无理由退换 · 顺丰速运 · <a href="products.html">选购好物 ›</a>`;
  }

  // ---- 商品详情 ----
  function renderProductDetail() {
    const root = $('#detailRoot');
    if (!root) return;
    const id = +new URLSearchParams(location.search).get('id') || 1;
    const p = PRODUCTS.find(x => x.id === id) || PRODUCTS[0];

    root.innerHTML = `
      <div class="detail-hero">
        <div class="eyebrow">P-${String(p.id).padStart(4, '0')} · ${p.tag || '现货'}</div>
        <h1>${p.name.split(' ').slice(0, 3).join(' ')}</h1>
        <div class="lede">${p.sales.toLocaleString()} 人已购 · 顺丰发货 · 7 天无理由</div>
        <div class="cta-row">
          <span class="price-big"><span class="sym">¥</span>${p.price.toLocaleString()}</span>
          ${p.oldPrice ? `<span class="price-old">¥${p.oldPrice.toLocaleString()}</span>` : ''}
          <button class="btn-pill" id="buyNow">立即购买</button>
          <a class="btn-link" href="cart.html">查看购物车 ›</a>
        </div>
        <div class="hero-product">
          <div class="product-shot">${p.emoji}</div>
        </div>
      </div>

      <section class="duo-section">
        <div class="duo">
          <div class="duo-card">
            <div class="name">售后保障</div>
            <div class="lede">7 天无理由退换 · 全国联保</div>
            <div class="glyph">✅</div>
          </div>
          <div class="duo-card">
            <div class="name">极速发货</div>
            <div class="lede">顺丰速运 · 24 小时必达</div>
            <div class="glyph">🚀</div>
          </div>
        </div>
      </section>

      <section class="detail-info-section">
        <div class="info-grid">
          <div class="info-block">
            <div class="label">规格</div>
            <div class="spec-options">
              <button class="spec active">标配</button>
              <button class="spec">套装版</button>
              <button class="spec">尊享版</button>
            </div>
          </div>
          <div class="info-block">
            <div class="label">数量</div>
            <div class="qty-stepper">
              <button class="dec">−</button>
              <span class="q">1</span>
              <button class="inc">+</button>
            </div>
          </div>
          <div class="info-block">
            <div class="label">配送</div>
            <div class="value">满 99 全国包邮 · 顺丰 24h</div>
          </div>
          <div class="info-block">
            <div class="label">评分</div>
            <div class="value">★ ${p.rating} · ${p.sales.toLocaleString()} 评价</div>
          </div>
        </div>

        <div class="cta-block">
          <button class="btn-pill" id="addCart">加入购物车</button>
          <a class="btn-link" href="cart.html">前往结算 ›</a>
        </div>

        <div class="desc-block">
          <h2>关于此商品</h2>
          <p>来自 <b>${p.shop}</b> 的官方正品。每一件商品均经过严格质检，支持 7 天无理由退换、顺丰速运、官方保修。编号 P-${String(p.id).padStart(4, '0')}。</p>
        </div>
      </section>

      <section class="related">
        <div class="section-head">
          <div class="eyebrow">YOU MIGHT ALSO LIKE</div>
          <h2>你可能也 <em class="text-blue">喜欢</em></h2>
          <div class="lede">更多精选好物，等待发现。</div>
        </div>
        <div class="product-grid" id="relatedGrid"></div>
      </section>
    `;

    renderProductGrid($('#relatedGrid'), PRODUCTS.filter(x => x.id !== p.id).slice(0, 4));

    let qty = 1;
    $('.qty-stepper .inc').onclick = () => { qty++; $('.qty-stepper .q').textContent = qty; };
    $('.qty-stepper .dec').onclick = () => { if (qty > 1) { qty--; $('.qty-stepper .q').textContent = qty; } };
    $$('.spec').forEach(b => b.onclick = () => {
      $$('.spec').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
    });
    $('#addCart').onclick = () => {
      addToCart(p.id, qty);
      flashToast('已加入购物车 · qty ' + qty);
    };
    $('#buyNow').onclick = () => {
      addToCart(p.id, qty);
      location.href = 'cart.html';
    };
  }

  // ---- 商品列表 ----
  function renderProductsPage() {
    const grid = $('#grid');
    if (!grid) return;
    const params = new URLSearchParams(location.search);
    const cat = params.get('cat');
    const q   = (params.get('q') || '').trim().toLowerCase();

    let list = PRODUCTS.slice();
    if (cat) {
      const catMap = {
        ELEC:   ['📱', '💻', '🎧', '⌚', '📺', '🖱️'],
        HOME:   ['🧹', '❄️', '🧊', '🫧', '💇'],
        STYLE:  ['👟', '🧥'],
        BEAUTY: ['💄', '💋', '🧴', '🧖'],
        FOOD:   ['🥜', '🍶', '☕'],
      };
      const allowed = catMap[cat] || catMap[cat?.toUpperCase()];
      if (allowed) list = list.filter(p => allowed.includes(p.emoji));
    }
    if (q) list = list.filter(p => p.name.toLowerCase().includes(q) || p.shop.toLowerCase().includes(q));

    const sortSel = $('#sort');
    if (sortSel) {
      sortSel.value = params.get('sort') || 'default';
      sortSel.onchange = () => {
        const v = sortSel.value;
        params.set('sort', v);
        location.search = '?' + params.toString();
      };
      const v = sortSel.value;
      if (v === 'price-asc')  list.sort((a, b) => a.price - b.price);
      if (v === 'price-desc') list.sort((a, b) => b.price - a.price);
      if (v === 'sales')      list.sort((a, b) => b.sales - a.sales);
      if (v === 'rating')     list.sort((a, b) => b.rating - a.rating);
    }

    const info = $('#listInfo');
    if (info) {
      info.innerHTML = `共 <b>${list.length}</b> 件商品${
        cat ? ' · 分类 <b>' + cat + '</b>' : ''
      }${q ? ' · 关键词 <b>' + q + '</b>' : ''}`;
    }

    renderProductGrid(grid, list);
  }

  // ---- 购物车 ----
  function renderCartPage() {
    const root = $('#cartRoot');
    if (!root) return;
    const cart = getCart();
    if (cart.length === 0) {
      root.innerHTML = `
        <div class="empty-cart">
          <div class="big">购物车是空的</div>
          <p>挑点喜欢的商品加入购物车吧。</p>
          <a class="btn-pill" href="products.html">去选购</a>
        </div>
      `;
      return;
    }
    root.innerHTML = `
      <div class="cart-head">
        <label class="checkall"><input type="checkbox" id="checkAll" ${cart.every(x => x.checked) ? 'checked' : ''}> <span>全选</span></label>
        <div>商品</div>
        <div>单价</div>
        <div>数量</div>
        <div>小计</div>
        <div>操作</div>
      </div>
      <div class="cart-list" id="cartList"></div>
      <div class="cart-foot">
        <div class="left">
          <label class="checkall"><input type="checkbox" id="checkAll2" ${cart.every(x => x.checked) ? 'checked' : ''}> <span>全选</span></label>
        </div>
        <div class="right">
          <span>合计 <span class="big" id="selSum">¥0</span></span>
          <a class="btn-pill" href="checkout.html">去结算</a>
        </div>
      </div>
    `;
    const list = $('#cartList');
    function paint() {
      const c = getCart();
      list.innerHTML = c.map(it => `
        <div class="cart-row" data-id="${it.id}">
          <label class="row-check"><input type="checkbox" ${it.checked ? 'checked' : ''}></label>
          <div class="row-goods">
            <div class="thumb">${it.emoji}</div>
            <div class="meta">
              <div class="name">${it.name}</div>
              <div class="spec">${it.spec}</div>
            </div>
          </div>
          <div class="row-price">¥${it.price.toLocaleString()}</div>
          <div class="row-qty">
            <button class="qty-dec">−</button>
            <span>${it.qty}</span>
            <button class="qty-inc">+</button>
          </div>
          <div class="row-sum">¥${(it.price * it.qty).toLocaleString()}</div>
          <button class="row-del" title="删除">×</button>
        </div>
      `).join('');
      const tot = cartTotal();
      $('#selSum').textContent = fmt(tot.sum);
      $$('.cart-row').forEach(row => {
        const id = +row.dataset.id;
        row.querySelector('input[type=checkbox]').onchange = e =>
          toggleCheck(id, e.target.checked);
        row.querySelector('.qty-dec').onclick = () => { updateQty(id, -1); paint(); };
        row.querySelector('.qty-inc').onclick = () => { updateQty(id, +1); paint(); };
        row.querySelector('.row-del').onclick = () => { removeCart(id); paint(); };
      });
    }
    paint();
    $('#checkAll').onchange = e => { toggleAll(e.target.checked); paint(); };
    $('#checkAll2').onchange = e => { toggleAll(e.target.checked); paint(); };
  }

  // ---- 结算 ----
  function renderCheckoutPage() {
    const root = $('#checkoutRoot');
    if (!root) return;
    const cart = getCart().filter(x => x.checked);
    if (cart.length === 0) {
      root.innerHTML = `<div class="empty-cart"><div class="big">购物车是空的</div><p>请先挑选商品。</p><a class="btn-pill" href="products.html">去选购</a></div>`;
      return;
    }
    root.innerHTML = `
      <div class="checkout-grid">
        <div class="left">
          <div class="block">
            <div class="block-title">收货地址</div>
            <div class="addr-list" id="addrList"></div>
          </div>
          <div class="block">
            <div class="block-title">支付方式</div>
            <div class="pay-grid">
              <label class="pay-opt active"><input type="radio" name="pay" checked><span>数字人民币</span></label>
              <label class="pay-opt"><input type="radio" name="pay"><span>支付宝</span></label>
              <label class="pay-opt"><input type="radio" name="pay"><span>微信支付</span></label>
              <label class="pay-opt"><input type="radio" name="pay"><span>信用卡</span></label>
            </div>
          </div>
          <div class="block">
            <div class="block-title">商品清单</div>
            <div class="co-items" id="coItems"></div>
          </div>
        </div>
        <div class="right">
          <div class="block summary">
            <div class="block-title">订单摘要</div>
            <div class="sum-row"><span>件数</span><b id="coCount"></b></div>
            <div class="sum-row"><span>总价</span><b id="coSum"></b></div>
            <div class="sum-row"><span>运费</span><b>免运费</b></div>
            <div class="sum-row big"><span>应付</span><b id="coTotal"></b></div>
            <button class="btn-pill" id="submitOrder" style="width:100%;margin-top:12px;padding:14px">确认下单</button>
          </div>
        </div>
      </div>
    `;
    $('#addrList').innerHTML = ADDRESSES.map(a => `
      <label class="addr-card ${a.isDefault ? 'active' : ''}">
        <input type="radio" name="addr" ${a.isDefault ? 'checked' : ''}>
        <div class="addr-info">
          <div class="top">
            <b>${a.name}</b>
            <span>${a.phone}</span>
            ${a.isDefault ? '<span class="tag-default">默认</span>' : ''}
          </div>
          <div class="addr-detail">${a.province} ${a.city} ${a.district} · ${a.detail}</div>
        </div>
      </label>
    `).join('');
    $('#coItems').innerHTML = cart.map(it => `
      <div class="co-item">
        <div class="thumb">${it.emoji}</div>
        <div class="info">
          <div class="name">${it.name}</div>
          <div class="spec">${it.spec}</div>
        </div>
        <div class="qty">× ${it.qty}</div>
        <div class="price">¥${(it.price * it.qty).toLocaleString()}</div>
      </div>
    `).join('');
    const sum = cart.reduce((s, x) => s + x.price * x.qty, 0);
    const cnt = cart.reduce((s, x) => s + x.qty, 0);
    $('#coCount').textContent = cnt;
    $('#coSum').textContent   = fmt(sum);
    $('#coTotal').textContent = fmt(sum);
    $('#submitOrder').onclick = () => {
      flashToast('下单成功 · 跳转中');
      setTimeout(() => location.href = 'user.html', 800);
    };
    $$('.pay-opt').forEach(o => o.onclick = () => {
      $$('.pay-opt').forEach(x => x.classList.remove('active'));
      o.classList.add('active');
    });
  }

  // ---- 用户中心 ----
  function renderUserPage() {
    const root = $('#userRoot');
    if (!root) return;
    root.innerHTML = `
      <aside class="user-side">
        <div class="avatar">U</div>
        <div class="uname">user_8810</div>
        <div class="ulevel">Apple ID · 已登录</div>
        <ul class="user-menu">
          <li class="active" data-tab="orders">我的订单</li>
          <li data-tab="addr">收货地址</li>
          <li data-tab="fav">我的收藏</li>
          <li data-tab="coupon">优惠券</li>
          <li data-tab="msg">系统消息</li>
        </ul>
      </aside>

      <section class="user-main">
        <div class="panel-block" id="ordersTab">
          <div class="block-title">最近订单</div>
          <div id="orderList"></div>
        </div>
        <div class="panel-block" id="addrTab" hidden>
          <div class="block-title">收货地址</div>
          <div id="addrList2"></div>
        </div>
        <div class="panel-block" id="favTab" hidden>
          <div class="block-title">我的收藏</div>
          <div class="product-grid" id="favGrid"></div>
        </div>
        <div class="panel-block" id="couponTab" hidden>
          <div class="block-title">优惠券</div>
          <div class="coupon-grid" id="couponGrid"></div>
        </div>
        <div class="panel-block" id="msgTab" hidden>
          <div class="block-title">系统消息</div>
          <ul class="msg-list">
            <li><span class="time">07-23 09:12</span> 您的订单 XC202607220001 已发货，顺丰单号 SF1234567890</li>
            <li><span class="time">07-22 18:30</span> 系统升级完成，新增多端同步功能</li>
            <li><span class="time">07-21 14:08</span> 您领取的优惠券「满 1000 减 100」即将到期</li>
            <li><span class="time">07-20 09:00</span> 欢迎加入 Pure · 新人立享 ¥50 礼包</li>
          </ul>
        </div>
      </section>
    `;
    $('#orderList').innerHTML = ORDERS.map(o => `
      <div class="order-card">
        <div class="order-head">
          <div>
            <span class="oid">${o.id}</span>
            <span class="otime">${o.time}</span>
          </div>
          <span class="status ${o.statusClass}">${o.status}</span>
        </div>
        <div class="order-body">
          ${o.items.map(it => `
            <div class="oi">
              <div class="thumb">${it.emoji}</div>
              <div class="info">
                <div class="n">${it.name}</div>
                <div class="s">${it.spec} · × ${it.qty}</div>
              </div>
              <div class="p">¥${(it.price * it.qty).toLocaleString()}</div>
            </div>
          `).join('')}
        </div>
        <div class="order-foot">
          <span>共 ${o.items.reduce((s, x) => s + x.qty, 0)} 件 · 合计 <b>¥${o.total.toLocaleString()}</b></span>
          <a class="btn-link" href="#">查看详情 ›</a>
        </div>
      </div>
    `).join('');
    $('#addrList2').innerHTML = ADDRESSES.map(a => `
      <div class="addr-row">
        <div>
          <b>${a.name}</b> <span>${a.phone}</span>
          ${a.isDefault ? '<span class="tag-default">默认</span>' : ''}
        </div>
        <div class="addr-detail">${a.province} ${a.city} ${a.district} · ${a.detail}</div>
      </div>
    `).join('');
    renderProductGrid($('#favGrid'), PRODUCTS.slice(0, 4));

    const coupons = [
      { val: 100, req: 1000 },
      { val: 50,  req: 300 },
      { val: 20,  req: 99 },
      { val: 200, req: 2000 },
    ];
    $('#couponGrid').innerHTML = coupons.map(c => `
      <div class="coupon">
        <div class="left">
          <span class="sym">¥</span>
          <span class="num">${c.val}</span>
        </div>
        <div class="right">
          <div class="req">满 ¥${c.req} 可用</div>
        </div>
      </div>
    `).join('');

    $$('.user-menu li').forEach(li => {
      li.onclick = () => {
        $$('.user-menu li').forEach(x => x.classList.remove('active'));
        li.classList.add('active');
        const t = li.dataset.tab;
        ['orders', 'addr', 'fav', 'coupon', 'msg'].forEach(k => {
          const node = $('#' + k + 'Tab');
          if (node) node.hidden = k !== t;
        });
      };
    });
  }

  // ---- 首页 ----
  function renderHome() {
    renderCategories($('#catGrid'));
    renderProductGrid($('#featuredGrid'), PRODUCTS.slice(0, 8));
    renderMarquee($('#marqueeTrack'));
  }

  document.addEventListener('DOMContentLoaded', () => {
    updateCartBadge();
    renderHome();
    renderProductsPage();
    renderProductDetail();
    renderCartPage();
    renderCheckoutPage();
    renderUserPage();
  });

  return { addToCart, getCart, saveCart, cartTotal, fmt, PRODUCTS };
})();