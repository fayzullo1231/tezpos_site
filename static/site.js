(() => {
  const formatMoney = (value) => {
    const num = Number(value) || 0;
    return `${num.toLocaleString("ru-RU")} so'm`;
  };

  const billingButtons = document.querySelectorAll(".billing-btn");
  const prices = document.querySelectorAll(".plan-price");
  const cycles = document.querySelectorAll(".plan-cycle");

  const updateCycle = (mode) => {
    prices.forEach((node) => {
      const monthly = Number(node.dataset.monthly || "0");
      const yearly = monthly * 10;
      node.textContent = mode === "yearly" ? formatMoney(yearly) : formatMoney(monthly);
    });
    cycles.forEach((node) => {
      node.textContent = mode === "yearly" ? "/yiliga" : "/oyiga";
    });
  };

  if (billingButtons.length) {
    billingButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        billingButtons.forEach((b) => b.classList.remove("is-active"));
        btn.classList.add("is-active");
        updateCycle(btn.dataset.cycle || "monthly");
      });
    });
    updateCycle("monthly");
  }

  const tiltCards = document.querySelectorAll(".tilt-card");
  tiltCards.forEach((card) => {
    card.addEventListener("mousemove", (event) => {
      const rect = card.getBoundingClientRect();
      const px = (event.clientX - rect.left) / rect.width;
      const py = (event.clientY - rect.top) / rect.height;
      const rotateY = (px - 0.5) * 10;
      const rotateX = (0.5 - py) * 10;
      card.style.transform = `perspective(900px) rotateX(${rotateX}deg) rotateY(${rotateY}deg) translateZ(8px)`;
    });

    card.addEventListener("mouseleave", () => {
      card.style.transform = "perspective(900px) rotateX(0deg) rotateY(0deg) translateZ(0)";
    });
  });

  const hero3d = document.querySelector("#hero3d");
  if (hero3d) {
    const layers = hero3d.querySelectorAll(".scene-layer");
    hero3d.addEventListener("mousemove", (event) => {
      const rect = hero3d.getBoundingClientRect();
      const dx = (event.clientX - rect.left) / rect.width - 0.5;
      const dy = (event.clientY - rect.top) / rect.height - 0.5;
      layers.forEach((layer, index) => {
        const depth = (index + 1) * 14;
        layer.style.transform = `translate3d(${dx * depth}px, ${dy * depth}px, 0)`;
      });
    });
    hero3d.addEventListener("mouseleave", () => {
      layers.forEach((layer) => {
        layer.style.transform = "translate3d(0, 0, 0)";
      });
    });
  }

  const revealTargets = document.querySelectorAll(".reveal-on-scroll");
  if (revealTargets.length) {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.15 }
    );

    revealTargets.forEach((item) => observer.observe(item));
  }
})();
