(() => {
  "use strict";

  // State capitals plus commonly used cities make the address fields useful
  // without requiring a network request or a third-party autocomplete service.
  const CITIES_BY_STATE = {
    "Abia": ["Aba", "Ohafia", "Umuahia"],
    "Adamawa": ["Ganye", "Jimeta", "Mubi", "Yola"],
    "Akwa Ibom": ["Eket", "Ikot Ekpene", "Oron", "Uyo"],
    "Anambra": ["Awka", "Nnewi", "Onitsha"],
    "Bauchi": ["Azare", "Bauchi", "Jama'are", "Misau"],
    "Bayelsa": ["Brass", "Ogbia", "Yenagoa"],
    "Benue": ["Gboko", "Katsina-Ala", "Makurdi", "Otukpo"],
    "Borno": ["Bama", "Biu", "Maiduguri", "Monguno"],
    "Cross River": ["Calabar", "Ikom", "Obudu", "Ogoja"],
    "Delta": ["Asaba", "Sapele", "Ughelli", "Warri"],
    "Ebonyi": ["Abakaliki", "Afikpo", "Onueke"],
    "Edo": ["Auchi", "Benin City", "Ekpoma", "Uromi"],
    "Ekiti": ["Ado Ekiti", "Ikere Ekiti", "Oye Ekiti"],
    "Enugu": ["Enugu", "Nsukka", "Oji River"],
    "Federal Capital Territory": ["Abuja", "Gwagwalada", "Kubwa", "Kuje", "Maitama"],
    "Gombe": ["Bajoga", "Gombe", "Kaltungo", "Kumo"],
    "Imo": ["Mbaitoli", "Oguta", "Orlu", "Owerri"],
    "Jigawa": ["Dutse", "Gumel", "Hadejia", "Kazaure"],
    "Kaduna": ["Kachia", "Kaduna", "Kafanchan", "Zaria"],
    "Kano": ["Bichi", "Kano", "Rano", "Wudil"],
    "Katsina": ["Daura", "Funtua", "Katsina", "Malumfashi"],
    "Kebbi": ["Argungu", "Birnin Kebbi", "Jega", "Yauri"],
    "Kogi": ["Anyigba", "Idah", "Kabba", "Lokoja", "Okene"],
    "Kwara": ["Ilorin", "Jebba", "Offa", "Omu-Aran"],
    "Lagos": ["Agege", "Badagry", "Epe", "Ikeja", "Ikorodu", "Lagos", "Lekki", "Surulere"],
    "Nasarawa": ["Keffi", "Lafia", "Nasarawa", "Akwanga"],
    "Niger": ["Bida", "Kontagora", "Minna", "Suleja"],
    "Ogun": ["Abeokuta", "Ijebu Ode", "Ota", "Sagamu"],
    "Ondo": ["Akure", "Ikare", "Ondo", "Owo"],
    "Osun": ["Ede", "Ife", "Ilesa", "Osogbo"],
    "Oyo": ["Ibadan", "Iseyin", "Ogbomoso", "Oyo"],
    "Plateau": ["Barkin Ladi", "Bukuru", "Jos", "Pankshin"],
    "Rivers": ["Bonny", "Eleme", "Obio-Akpor", "Port Harcourt"],
    "Sokoto": ["Goronyo", "Sokoto", "Tambuwal", "Wamako"],
    "Taraba": ["Bali", "Jalingo", "Serti", "Wukari"],
    "Yobe": ["Damaturu", "Gashua", "Nguru", "Potiskum"],
    "Zamfara": ["Gusau", "Kaura Namoda", "Talata Mafara", "Tsafe"],
  };

  const createDatalist = (id) => {
    const list = document.createElement("datalist");
    list.id = id;
    document.body.appendChild(list);
    return list;
  };

  const addOptions = (list, values) => {
    list.replaceChildren(...values.map((value) => {
      const option = document.createElement("option");
      option.value = value;
      return option;
    }));
  };

  document.querySelectorAll("[data-nigeria-state]").forEach((stateField, index) => {
    const form = stateField.form || document;
    const cityField = form.querySelector("[data-nigeria-city]");
    if (!cityField) return;

    const stateList = createDatalist(`nigeria-states-${index}`);
    const cityList = createDatalist(`nigeria-cities-${index}`);
    addOptions(stateList, Object.keys(CITIES_BY_STATE));
    stateField.setAttribute("list", stateList.id);
    cityField.setAttribute("list", cityList.id);
    stateField.placeholder = "Search or select a state";

    const updateCities = () => {
      const cities = CITIES_BY_STATE[stateField.value];
      addOptions(cityList, cities || []);
      cityField.disabled = !cities;
      cityField.placeholder = cities ? "Search or select a city" : "Select a Nigerian state first";
      if (cityField.value && cities && !cities.includes(cityField.value)) cityField.value = "";
    };

    stateField.addEventListener("input", updateCities);
    stateField.addEventListener("change", updateCities);
    updateCities();
  });
})();
