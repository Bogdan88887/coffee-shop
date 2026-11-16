/* -----------------
Stop Scrolling && Page Loader 
--------------*/

// left: 37, up: 38, right: 39, down: 40,
// spacebar: 32, pageup: 33, pagedown: 34, end: 35, home: 36
var keys = {
    37: 1,
    38: 1,
    39: 1,
    40: 1
};

function preventDefault(e) {
    e.preventDefault();
}

function preventDefaultForScrollKeys(e) {
    if (keys[e.keyCode]) {
        preventDefault(e);
        return false;
    }
}

// modern Chrome requires { passive: false } when adding event
var supportsPassive = false;
try {
    window.addEventListener("test", null, Object.defineProperty({}, 'passive', {
        get: function () {
            supportsPassive = true;
        }
    }));
} catch (e) {}

var wheelOpt = supportsPassive ? {
    passive: false
} : false;
var wheelEvent = 'onwheel' in document.createElement('div') ? 'wheel' : 'mousewheel';

// call this to Disable
function disableScroll() {
    window.addEventListener('DOMMouseScroll', preventDefault, false); // older FF
    window.addEventListener(wheelEvent, preventDefault, wheelOpt); // modern desktop
    window.addEventListener('touchmove', preventDefault, wheelOpt); // mobile
    window.addEventListener('keydown', preventDefaultForScrollKeys, false);
}

// call this to Enable
function enableScroll() {
    window.removeEventListener('DOMMouseScroll', preventDefault, false);
    window.removeEventListener(wheelEvent, preventDefault, wheelOpt);
    window.removeEventListener('touchmove', preventDefault, wheelOpt);
    window.removeEventListener('keydown', preventDefaultForScrollKeys, false);
}



window.addEventListener("load", function () {
    /* -------------------- Page Loader--------------------- */
    document.querySelector(".pageLoader").classList.add("fade-out");
    disableScroll();
    setTimeout(function () {
        document.querySelector(".pageLoader").style.display = "none";
        enableScroll();
    }, 2000);
});


/* -----------------
Toggle Navbar 
--------------*/
const navToggler = document.querySelector(".nav-toggler");
navToggler.addEventListener("click", toggleNav);

function toggleNav() {
    navToggler.classList.toggle("active");
    document.querySelector(".navbar").classList.toggle("active");
}

/* -----------------
Close Nav when clicking on nav item 
--------------*/
document.addEventListener("click", function (e) {
    if (e.target.closest(".navbar a")) {
        toggleNav();
    }
});

/* -----------------
Navbar Sticky 
--------------*/
window.addEventListener("scroll", function () {
    if (this.scrollY > 60) {
        document.querySelector(".header").classList.add("sticky");
    } else {
        document.querySelector(".header").classList.remove("sticky");
    }
});

/* -----------------
Home Section Slider 
--------------*/
document.querySelectorAll(".imgSlider img").forEach(images => {

    images.onclick = () => {
        var src = images.getAttribute('src');
        document.querySelector(".mainHomeImg").src = src;
    };
});

/* -----------------
Initialize Swiper Slider 
--------------*/
var swiper = new Swiper(".reviewSlider", {

    spaceBetween: 20,
    pagination: {
        el: ".swiper-pagination",
        dynamicBullets: true,
        clickable: true,
    },
    autoplay: {
        delay: 3500,
        disableOnInteraction: false,
    },
    breakpoints: {
        767: {
            slidesPerView: 1,
        },
        768: {
            slidesPerView: 2,
        },
    },
    keyboard: {
        enabled: true,
    },
    // effect: "flip",
    loop: true,
    grabCursor: true,
});

/* -----------------
Trigger To Top Button
--------------*/

$(window).scroll(function () {
    if ($(this).scrollTop()) {
        $('#toTop').fadeIn();
    } else {
        $('#toTop').fadeOut();
    }
});

$("#toTop").click(function () {
    //1 second of animation time
    //html works for FFX but not Chrome
    //body works for Chrome but not FFX
    //This strange selector seems to work universally
    $("html, body").animate({
        scrollTop: 0
    }, 40);
});

/* -----------------
Prevent anchor Link Behavior
--------------*/
$("#disabledLink").click(function (ev) {
    ev.preventDefault();
});

/* -----------------
Set attribute to all links
--------------*/
// var links = document.getElementsByTagName('a');
// var len = links.length;

// for (var i = 0; i < len; i++) {
//     links[i].target = "_blank";
// }

const form = document.getElementById("commentForm");
const reviewsWrapper = document.getElementById("reviewsWrapper");
const authPanel = document.getElementById("authPanel");

if (form && reviewsWrapper && authPanel) {
    const registerForm = document.getElementById("registerForm");
    const loginForm = document.getElementById("loginForm");
    const logoutButton = document.getElementById("logoutButton");
    const authForms = document.getElementById("authForms");
    const authMessage = document.getElementById("authMessage");
    const commentAvatars = Array.from({ length: 10 }, (_, index) =>
        `img/avatars/avatar-${String(index + 1).padStart(2, "0")}.svg`
    );
    const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (character) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
    })[character]);

    async function loadComments() {
        const res = await fetch("/api/comments");
        if (!res.ok) throw new Error("Не удалось загрузить отзывы.");
        const comments = await res.json();

        reviewsWrapper.innerHTML = "";

        comments.forEach(comment => {
            const avatarPath = typeof comment.avatar === "string" && /^img\/avatars\/avatar-\d{2}\.svg$/.test(comment.avatar)
                ? comment.avatar
                : commentAvatars[Math.floor(Math.random() * commentAvatars.length)];
            const avatar = escapeHtml(avatarPath);
            reviewsWrapper.innerHTML += `
                <div class="swiper-slide box">
                    <i class="fas fa-quote-left"></i>
                    <i class="fas fa-quote-right"></i>

                    <img src="${escapeHtml(avatar)}" alt="Coffee and nature illustration">

                    <div class="stars">
                        <i class="fas fa-star"></i>
                        <i class="fas fa-star"></i>
                        <i class="fas fa-star"></i>
                        <i class="fas fa-star"></i>
                        <i class="fas fa-star"></i>
                    </div>

                    <p>${escapeHtml(comment.message)}</p>

                    <h3>${escapeHtml(comment.name)}</h3>
                    <span>${escapeHtml(comment.date)}</span>
                </div>
            `;
        });

        swiper.update();
    }

    async function refreshAuthState() {
        const response = await fetch("/api/auth/me");
        const state = await response.json();
        const signedIn = Boolean(state.username);
        form.hidden = !signedIn;
        authForms.hidden = signedIn;
        logoutButton.hidden = !signedIn;
        document.getElementById("name").value = state.username || "";
        authMessage.textContent = signedIn
            ? `Вы вошли как ${state.username}. Теперь можно оставить отзыв.`
            : "Войдите в аккаунт, чтобы написать отзыв. Отзывы других гостей доступны всем.";
    }

    async function sendAuthForm(url, payload) {
        const response = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Не удалось выполнить запрос.");
        return result;
    }

    registerForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
            await sendAuthForm("/api/auth/register", {
                username: document.getElementById("registerUsername").value,
                password: document.getElementById("registerPassword").value
            });
            alert("Регистрация завершена. Теперь войдите с этим логином и паролем.");
            registerForm.reset();
        } catch (error) { alert(error.message); }
    });

    loginForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
            await sendAuthForm("/api/auth/login", {
                username: document.getElementById("loginUsername").value,
                password: document.getElementById("loginPassword").value
            });
            loginForm.reset();
            await refreshAuthState();
        } catch (error) { alert(error.message); }
    });

    logoutButton.addEventListener("click", async () => {
        try {
            await sendAuthForm("/api/auth/logout", {});
            await refreshAuthState();
        } catch (error) { alert(error.message); }
    });

    form.addEventListener("submit", async (e) => {
        e.preventDefault();

        const comment = {
            message: document.getElementById("message").value
        };

        try {
            const res = await fetch("/api/comments", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify(comment)
            });
            const result = await res.json();
            if (!res.ok) throw new Error(result.error || "Не удалось отправить отзыв.");

            form.reset();
            await loadComments();
        } catch (error) {
            alert(error.message);
        }
    });

    Promise.all([loadComments(), refreshAuthState()]).catch((error) => console.error(error));

    // Автоматически подгружаем свежие отзывы, не заставляя пользователя обновлять страницу.
    window.setInterval(() => loadComments().catch((error) => console.error(error)), 30000);
}
